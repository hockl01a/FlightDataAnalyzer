import logging

from datetime import datetime, timedelta
from inspect import isclass

import numpy as np
from analysis_engine import hooks, settings, __version__
from analysis_engine.dependency_graph import dependency_order, graph_adjacencies
from analysis_engine.library import np_ma_masked_zeros_like
from analysis_engine.node import (Attribute, derived_param_from_hdf,
                                  DerivedParameterNode,
                                  FlightAttributeNode,
                                  FlightPhaseNode,
                                  KeyPointValueNode,
                                  KeyTimeInstanceNode, Node,
                                  NodeManager, P, SectionNode)
from hdfaccess.file import hdf_file

logger = logging.getLogger(__name__)

def geo_locate(hdf, kti_list):
    """
    Translate KeyTimeInstance into GeoKeyTimeInstance namedtuples
    """
    if 'Latitude Smoothed' not in hdf \
       or 'Longitude Smoothed' not in hdf:
        return kti_list
    
    lat_pos = derived_param_from_hdf(hdf, 'Latitude Smoothed')
    long_pos = derived_param_from_hdf(hdf, 'Longitude Smoothed')
    
    for kti in kti_list:
        kti.latitude = lat_pos.at(kti.index)
        kti.longitude = long_pos.at(kti.index)
    return kti_list


def _timestamp(start_datetime, item_list):
    """
    Adds item.datetime (from timedelta of item.index + start_datetime)
    
    :param start_datetime: Origin timestamp used as a base to the index
    :type start_datetime: datetime
    :param item_list: list of objects with a .index attribute
    :type item_list: list
    """
    for item in item_list:
        item.datetime = start_datetime + timedelta(seconds=float(item.index))
    return item_list


def derive_parameters(hdf, node_mgr, process_order):
    """
    Derives the parameter values and if limits are available, applies
    parameter validation upon each param before storing the resulting masked
    array back into the hdf file.
    
    :param hdf: Data file accessor used to get and save parameter data and attributes
    :type hdf: hdf_file
    :param node_mgr: Used to determine the type of node in the process_order
    :type node_mgr: NodeManager
    :param process_order: Parameter / Node class names in the required order to be processed
    :type process_order: list of strings
    """
    params = {} # store all derived params that aren't masked arrays
    kpv_list = KeyPointValueNode() # duplicate storage, but maintaining types
    kti_list = KeyTimeInstanceNode()
    section_list = SectionNode()  # 'Node Name' : node()  pass in node.get_accessor()
    flight_attrs = []
    
    for param_name in process_order:
        if param_name in node_mgr.lfl:
            continue
        
        elif node_mgr.get_attribute(param_name) is not None:
            # add attribute to dictionary of available params
            ###params[param_name] = node_mgr.get_attribute(param_name) #TODO: optimise with only one call to get_attribute
            continue
        
        node_class = node_mgr.derived_nodes[param_name]  #NB raises KeyError if Node is "unknown"
        
        # build ordered dependencies
        deps = []
        node_deps = node_class.get_dependency_names()
        for dep_name in node_deps:
            if dep_name in params:  # already calculated KPV/KTI/Phase
                deps.append(params[dep_name])
            elif node_mgr.get_attribute(dep_name) is not None:
                deps.append(node_mgr.get_attribute(dep_name))
            elif dep_name in hdf:  # LFL/Derived parameter
                # all parameters (LFL or other) need get_aligned which is
                # available on DerivedParameterNode
                dp = derived_param_from_hdf(hdf, dep_name)
                deps.append(dp)
            else:  # dependency not available
                deps.append(None)
        if all([d == None for d in deps]):
            raise RuntimeError("No dependencies available - Nodes cannot "
                               "operate without ANY dependencies available! "
                               "Node: %s" % node_class.__name__)
        first_dep = next((d for d in deps if d is not None))

        # initialise node
        node = node_class(frequency=first_dep.frequency,
                          offset=first_dep.offset)
        logger.info("Processing parameter %s", param_name)
        # Derive the resulting value

        result = node.get_derived(deps)

        if node.node_type is KeyPointValueNode:
            #Q: track node instead of result here??
            params[param_name] = result
            for one_hz in result.get_aligned(P(frequency=1, offset=0)) or []:
                if not (0 <= one_hz.index <= hdf.duration):
                    raise IndexError("KPV '%s' index %.2f is not between 0 and %d",
                        one_hz.name, one_hz.index, hdf.duration)
                kpv_list.append(one_hz)
        elif node.node_type is KeyTimeInstanceNode:
            params[param_name] = result
            for one_hz in result.get_aligned(P(frequency=1, offset=0)) or []:
                if not (0 <= one_hz.index <= hdf.duration):
                    raise IndexError("KTI '%s' index %.2f is not between 0 and %d",
                        one_hz.name, one_hz.index, hdf.duration)
                kti_list.append(one_hz)
        elif node.node_type is FlightAttributeNode:
            params[param_name] = result
            try:
                flight_attrs.append(Attribute(result.name, result.value)) # only has one Attribute result
            except:
                logger.warning("Flight Attribute Node '%s' returned empty handed."%(param_name))
        elif issubclass(node.node_type, SectionNode):
            params[param_name] = result
            for one_hz in result.get_aligned(P(frequency=1, offset=0)) or []:
                slice_ = one_hz.slice
                if slice_.start and not (0 <= slice_.start <= hdf.duration) or \
                   slice_.stop and not (0 <= slice_.stop <= hdf.duration + 1):
                    raise IndexError("Section '%s' (%.2f, %.2f) does not lie between 0 and %d",
                        one_hz.name, slice_.start or 0, slice_.stop or hdf.duration, hdf.duration)
                if one_hz.start_edge and not 0 <= one_hz.start_edge <= hdf.duration:
                    raise IndexError("Section '%s' start_edge (%.2f) does not lie between 0 and %d",
                        one_hz.name, one_hz.start_edge, hdf.duration)
                if one_hz.stop_edge and not 0 <= one_hz.stop_edge <= hdf.duration + 1:
                    raise IndexError("Section '%s' stop_edge (%.2f) does not lie between 0 and %d",
                        one_hz.name, one_hz.stop_edge, hdf.duration)
                section_list.append(one_hz)
        elif issubclass(node.node_type, DerivedParameterNode):
            if hdf.duration:
                # check that the right number of results were returned
                # Allow a small tolerance. For example if duration in seconds
                # is 2822, then there will be an array length of  1411 at 0.5Hz and 706
                # at 0.25Hz (rounded upwards). If we combine two 0.25Hz
                # parameters then we will have an array length of 1412.
                expected_length = hdf.duration * result.frequency
                if result.array == None:
                    array_length = expected_length
                    # Where a parameter is wholly masked, we fill the HDF
                    # file with masked zeros to maintain structure.
                    result.array = np_ma_masked_zeros_like(np.ma.arange(expected_length))
                else:
                    array_length = len(result.array)
                length_diff = array_length - expected_length
                if length_diff == 0:
                    pass
                elif 0 < length_diff < 5:
                    logger.warning("Cutting excess data for parameter '%s'. Expected length was "
                                    "'%s' while resulting array length was '%s'.",
                                    param_name, expected_length, len(result.array))
                    result.array = result.array[:expected_length]
                else:
                    raise ValueError("Array length mismatch for parameter "
                                     "'%s'. Expected '%s', resulting array "
                                     "length '%s'." % (param_name,
                                                       expected_length,
                                                       array_length))
                
            hdf.set_param(result)
        else:
            raise NotImplementedError("Unknown Type %s" % node.__class__)
        continue
    return kti_list, kpv_list, section_list, flight_attrs


def get_derived_nodes(module_names):
    """ Get all nodes into a dictionary
    """
    def isclassandsubclass(value, classinfo):
        return isclass(value) and issubclass(value, classinfo)

    nodes = {}
    for name in module_names:
        #Ref:
        #http://code.activestate.com/recipes/223972-import-package-modules-at-runtime/
        # You may notice something odd about the call to __import__(): why is
        # the last parameter a list whose only member is an empty string? This
        # hack stems from a quirk about __import__(): if the last parameter is
        # empty, loading class "A.B.C.D" actually only loads "A". If the last
        # parameter is defined, regardless of what its value is, we end up
        # loading "A.B.C"
        ##abstract_nodes = ['Node', 'Derived Parameter Node', 'Key Point Value Node', 'Flight Phase Node'
        ##print 'importing', name
        module = __import__(name, globals(), locals(), [''])
        for c in vars(module).values():
            if isclassandsubclass(c, Node) \
                    and c.__module__ != 'analysis_engine.node':
                try:
                    nodes[c.get_name()] = c
                except TypeError:
                    #TODO: Handle the expected error of top level classes
                    # Can't instantiate abstract class DerivedParameterNode
                    # - but don't know how to detect if we're at that level without resorting to 'if c.get_name() in 'derived parameter node',..
                    logger.exception('Failed to import class: %s' % c.get_name())
    return nodes


def process_flight(hdf_path, aircraft_info, start_datetime=datetime.now(),
                   achieved_flight_record={}, required_params=[], draw=False):
    """
    For development, the definitive API is located here:
        "PolarisTaskManagement.test.tasks_mask.process_flight"
        
    sample aircraft_info API:
    {
        'Tail Number':  # Aircraft Registration
        'Identifier':  # Aircraft Ident
        'Manufacturer': # e.g. Boeing
        'Manufacturer Serial Number': #MSN
        'Model': # e.g. 737-808-ER
        'Series': # e.g. 737-800
        'Family': # e.g. 737
        'Flap Selections': # e.g. [0,18,24,30,33]
        'Frame': # e.g. 737-3C
        'Main Gear To Altitude Radio': # Distance in metres
        'Wing Span': # Distance in metres
    }
    
    sample achieved_flight_record API:
    {
        # TODO!
    }
    
    :param hdf_path: Path to HDF File
    :type hdf_pat: String
    
    :param aircraft: Aircraft specific attributes
    :type aircraft: dict
    
    :returns: See below:
    :rtype: Dict
    {
        'flight':[Attribute('name value')]  # sample: [Attribute('Takeoff Airport', {'id':1234, 'name':'Int. Airport'}, Attribute('Approaches', [4567,7890]), ...], 
        'kti':[GeoKeyTimeInstance('index name latitude longitude')] if lat/long available else [KeyTimeInstance('index name')]
        'kpv':[KeyPointValue('index value name slice')]
    }
    
    """
    logger.info("Processing: %s", hdf_path)
    # go through modules to get derived nodes
    derived_nodes = get_derived_nodes(settings.NODE_MODULES)
    required_params = \
        list(set(required_params).intersection(set(derived_nodes)))
    # if required_params isn't set, try using ALL derived_nodes!
    if not required_params:
        logger.info("No required_params declared, using all derived nodes")
        required_params = derived_nodes.keys()
    
    # include all flight attributes as required
    required_params = list(set(
        required_params + get_derived_nodes(
            ['analysis_engine.flight_attribute']).keys()))
        
    # open HDF for reading
    with hdf_file(hdf_path) as hdf:
        # Track nodes. Assume that all params in HDF are from LFL(!)
        node_mgr = NodeManager(start_datetime, hdf.valid_param_names(), required_params, 
                               derived_nodes, aircraft_info,
                               achieved_flight_record)
        # calculate dependency tree
        process_order, gr_st = dependency_order(node_mgr, draw=draw)
        if settings.CACHE_PARAMETER_MIN_USAGE:
            # find params used more than
            for node in gr_st.nodes():
                if node in node_mgr.derived_nodes:  # this includes KPV/KTIs but they'll be ignored by HDF
                    qty = len(gr_st.predecessors(node))
                    if qty > settings.CACHE_PARAMETER_MIN_USAGE:
                        hdf.cache_param_list.append(node)
            logging.info("HDF set to cache parameters: %s", hdf.cache_param_list)
            
                    
        if hooks.PRE_FLIGHT_ANALYSIS:
            logger.info("Performing PRE_FLIGHT_ANALYSIS actions: %s", 
                         hooks.PRE_FLIGHT_ANALYSIS.func_name)
            hooks.PRE_FLIGHT_ANALYSIS(hdf, aircraft_info, process_order)
        else:
            logger.info("No PRE_FLIGHT_ANALYSIS actions to perform")
        
        # derive parameters
        kti_list, kpv_list, section_list, flight_attrs = derive_parameters(
            hdf, node_mgr, process_order)
             
        # geo locate KTIs
        kti_list = geo_locate(hdf, kti_list)
        kti_list = _timestamp(start_datetime, kti_list)

        # timestamp KPVs
        kpv_list = _timestamp(start_datetime, kpv_list)
        
        # Store version of FlightDataAnalyser and dependency tree in HDF file.
        hdf.version = __version__
        hdf.dependency_tree = graph_adjacencies(gr_st)
        
    ##if draw:
        ### only import if required
        ##from analysis_engine.plot_flight import plot_flight
        ##plot_flight(hdf_path, kti_list, kpv_list, section_list)
        
    return {'flight' : flight_attrs, 
            'kti' : kti_list, 
            'kpv' : kpv_list,
            'phases' : section_list}


if __name__ == '__main__':
    import argparse
    from utilities.filesystem_tools import copy_file
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)    
    parser = argparse.ArgumentParser(description="Process a flight.")
    parser.add_argument('file', type=str,
                        help='Path of file to process.')
    parser.add_argument('-tail', dest='tail_number', type=str, default='G-ABCD',
                        help='Aircraft Tail Number for processing.')
    parser.add_argument('-frame', dest='frame', type=str, default=None,
                        help='Data frame name.')
    parser.add_argument('-p', dest='plot', action='store_true',
                        default=False, help='Plot flight onto a graph.')
    args = parser.parse_args()
    
    hdf_copy = copy_file(args.file, postfix='_process')
    process_flight(hdf_copy, {'Tail Number': args.tail_number,
                              'Precise Positioning': True,
                              'Frame': args.frame,
                              },
                   draw=args.plot)
