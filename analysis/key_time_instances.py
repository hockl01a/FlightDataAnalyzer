import numpy as np

from analysis.library import hysteresis, index_at_value
from analysis.node import KeyTimeInstanceNode, KTI, P, S

from settings import (CLIMB_THRESHOLD,
                      LANDING_ACCELERATION_THRESHOLD,
                      RATE_OF_CLIMB_FOR_LIFTOFF,
                      RATE_OF_CLIMB_FOR_TOUCHDOWN,
                      SLOPE_FOR_TOC_TOD,
                      TAKEOFF_ACCELERATION_THRESHOLD
                      )


def find_toc_tod(alt_data, ccd_slice, mode):
    '''
    :alt_data : numpy masked array of pressure altitude data
    : ccd_slice : slice of a climb/cruise/descent phase above FL100
    : mode : Either 'Climb' or 'Descent' to define which to select.
    '''
    
    # Find the maximum altitude in this slice to reduce the effort later
    peak_index = np.ma.argmax(alt_data[ccd_slice])
    
    if mode == 'Climb':
        section = slice(ccd_slice.start, ccd_slice.start+peak_index+1, None)
        slope = SLOPE_FOR_TOC_TOD
    else:
        section = slice(ccd_slice.start+peak_index, ccd_slice.stop, None)
        slope = -SLOPE_FOR_TOC_TOD
        
    # Quit if there is nothing to do here.
    if section.start == section.stop:
        raise ValueError, 'No range of data for top of climb or descent check'
        
    # Establish a simple monotonic timebase
    timebase = np.arange(len(alt_data[section]))
    # Then scale this to the required altitude data slope
    ramp = timebase * slope
    # For airborne data only, subtract the slope from the climb, then
    # the peak is at the top of climb or descent.
    return np.ma.argmax(alt_data[section] - ramp) + section.start


class BottomOfDescent(KeyTimeInstanceNode):
    def derive(self, alt_std=P('Altitude STD'),
               dlc=S('Descent Low Climb')):
        # In the case of descents without landing, this finds the minimum
        # point of the dip.
        for this_dlc in dlc:
            kti = np.ma.argmin(alt_std.array[this_dlc.slice])
            self.create_kti(kti + this_dlc.slice.start)
        
           
class ApproachAndLandingLowestPoint(KeyTimeInstanceNode):
    def derive(self, app_lands=S('Approach And Landing'),
               alt_std=P('Altitude STD')):
        # In the case of descents without landing, this finds the minimum
        # point of the dip.
        for app_land in app_lands:
            kti = np.ma.argmin(alt_std.array[app_land.slice])
            self.create_kti(kti + app_land.slice.start)
    

class ClimbStart(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL'), climbing=S('Climbing')):
        for climb in climbing:
            initial_climb_index = index_at_value(alt_aal.array, climb.slice,
                                                        CLIMB_THRESHOLD)
            self.create_kti(initial_climb_index)


class GoAround(KeyTimeInstanceNode):
    """
    In POLARIS we define a Go-Around as any descent below 3000ft followed by
    an increase of 500ft. This wide definition will identify more events than
    a tighter definition, however experience tells us that it is worth
    checking all these cases. For example, we have identified attemnpts to
    land on roads or at the wrong airport, EGPWS database errors etc from
    checking these cases.
    """
    
    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        # List the minimum required parameters. If 'Altitude Radio For Flight
        # Phases' is available, that's a bonus and we will use it, but it is
        # not required.
        if 'Altitude AAL For Flight Phases' in available \
           and 'Approach And Landing' in available \
           and 'Climb For Flight Phases' in available:
            return True
        else:
            return False
        
    # List the optimal parameter set here
    def derive(self, alt_AAL=P('Altitude AAL For Flight Phases'),
               alt_rad=P('Altitude Radio For Flight Phases'),
               approaches=S('Approach And Landing'),
               climb=P('Climb For Flight Phases')):
        for app in approaches:
            if np.ma.maximum(climb.array[app.slice]) > 500:
                # We must have been in an approach phase, then climbed at
                # least 500ft. Mark the lowest point.
                if alt_rad:
                    pit_index = np.ma.argmin(alt_rad.array[app.slice])
                else:
                    # In case this aircraft has no rad alt fitted
                    pit_index = np.ma.argmin(alt_AAL.array[app.slice])
                self.create_kti(app.slice.start+pit_index)


class LandingPeakDeceleration(KeyTimeInstanceNode):
    """
    The landing has been found already, including and the flare and a little
    of the turn off the runway. Here we find the point of maximum
    deceleration, as this should lie between the touchdown when the aircraft
    may be drifting and the turnoff which could be at high speed, but should
    be at a gentler deceleration. This is used to identify the heading and
    location of the landing, as these will be more stable at peak
    deceleration than at the actual point of touchdown where the aircraft may
    still be have drift on.
    """
    def derive(self, head=P('Heading Continuous'), landings=S('Landing'),  
               accel=P('Acceleration Forwards For Flight Phases')):
        for land in landings:
            peak_decel_index = np.ma.argmin(accel.array[land.slice])
            peak_decel_index += land.slice.start
            self.create_kti(peak_decel_index)


class TopOfClimb(KeyTimeInstanceNode):
    def derive(self, alt_std=P('Altitude STD'), 
               ccd=S('Climb Cruise Descent')):
        # This checks for the top of climb in each 
        # Climb/Cruise/Descent period of the flight.
        for ccd_phase in ccd:
            ccd_slice = ccd_phase.slice
            try:
                n_toc = find_toc_tod(alt_std.array, ccd_slice, 'Climb')
            except:
                # altitude data does not have an increasing section, so quit.
                break
            # if this is the first point in the slice, it's come from
            # data that is already in the cruise, so we'll ignore this as well
            if n_toc==0:
                break
            # Record the moment (with respect to this section of data)
            self.create_kti(n_toc)


class TopOfDescent(KeyTimeInstanceNode):
    def derive(self, alt_std=P('Altitude STD'), 
               ccd=S('Climb Cruise Descent')):
        # This checks for the top of descent in each 
        # Climb/Cruise/Descent period of the flight.
        for ccd_phase in ccd:
            ccd_slice = ccd_phase.slice
            try:
                n_tod = find_toc_tod(alt_std.array, ccd_slice, 'Descent')
            except:
                # altitude data does not have a decreasing section, so quit.
                break
            # if this is the last point in the slice, it's come from
            # data that ends in the cruise, so we'll ignore this too.
            if n_tod==ccd_slice.stop - 1:
                break
            # Record the moment (with respect to this section of data)
            self.create_kti(n_tod)


class FlapStateChanges(KeyTimeInstanceNode):
    NAME_FORMAT = 'Flap %(setting)d'
    
    def derive(self, flap=P('Flap')):
        # Mark all flap changes, irrespective of the aircraft type :o)
        previous = None
        for index, value in enumerate(flap.array):
            if value == previous:
                continue
            else:
                # Flap moved from previous setting, so record this change:
                self.create_kti(index, setting=value)


# ===============================================================

"""
Takeoff KTIs are derived from the Takeoff Phase
"""

class TakeoffTurnOntoRunway(KeyTimeInstanceNode):
    # The Takeoff flight phase is computed to start when the aircraft turns
    # onto the runway, so this KTI is just at the start of that phase.
    def derive(self, toffs=S('Takeoff')):
        for toff in toffs:
            self.create_kti(toff.slice.start)


class TakeoffStartAcceleration(KeyTimeInstanceNode):
    def derive(self, fwd_acc=P('Acceleration Forwards For Flight Phases'), toffs=S('Takeoff')):
        for toff in toffs:
            start_accel = index_at_value(fwd_acc.array, toff.slice, 
                                                TAKEOFF_ACCELERATION_THRESHOLD)
            self.create_kti(start_accel)

            
class Liftoff(KeyTimeInstanceNode):
    # TODO: This should use the real rate of climb, but for the Hercules (and
    # old 146s) the data isn't good enough so need to use this parameter.
    def derive(self, roc=P('Rate Of Climb For Flight Phases'),
              toffs=S('Takeoff')):
        for toff in toffs:
            lift_index = index_at_value(roc.array, toff.slice, 
                                              RATE_OF_CLIMB_FOR_LIFTOFF)
            self.create_kti(lift_index)
            

class InitialClimbStart(KeyTimeInstanceNode):
    # The Takeoff flight phase is computed to run up to the start of the
    # initial climb, so this KTI is just at the end of that phase.
    def derive(self, toffs=S('Takeoff')):
        for toff in toffs:
            self.create_kti(toff.slice.stop)


"""
Landing KTIs are derived from the Landing Phase
"""

class LandingStart(KeyTimeInstanceNode):
    # The Landing flight phase is computed to start passing through 50ft
    # (nominally), so this KTI is just at the end of that phase.
    def derive(self, landings=S('Landing')):
        for landing in landings:
            self.create_kti(landing.slice.start)


class TouchAndGo(KeyTimeInstanceNode):
    """
    In POLARIS we define a Touch and Go as a Go-Around that contacted the ground.
    """
    '''
    TODO: Write a proper version when we have a better landing condition.
    Written without tests, just to provide a node to get things going. 29/12/11 DJ
    '''

    def derive(self, alt_AAL=P('Altitude AAL For Flight Phases'),
               gas=S('Go Around')):
        for ga in gas:
            if np.ma.minimum(alt_AAL.array[ga.slice]) == 0.0:
                self.create_kti(ga.slice.start, 'Touch And Go')


class Touchdown(KeyTimeInstanceNode):
    # TODO: Establish whether this works satisfactorily. If there are
    # problems with this algorithm we could compute the rate of descent
    # backwards from the runway for greater accuracy.
    def derive(self, roc=P('Rate Of Climb'), landings=S('Landing')):
        for landing in landings:
            land_index = index_at_value(roc.array, landing.slice, 
                                        RATE_OF_CLIMB_FOR_TOUCHDOWN)
            self.create_kti(land_index)


class LandingTurnOffRunway(KeyTimeInstanceNode):
    # The Landing phase is computed to end when the aircraft turns off the
    # runway, so this KTI is just at the start of that phase.
    def derive(self, landings=S('Landing')):
        for landing in landings:
            self.create_kti(landing.slice.stop)


class LandingStartDeceleration(KeyTimeInstanceNode):
    def derive(self, fwd_acc=P('Acceleration Forwards For Flight Phases'), landings=S('Landing')):
        for landing in landings:
            start_decel_index = index_at_value(fwd_acc.array, landing.slice, 
                                                LANDING_ACCELERATION_THRESHOLD)
            self.create_kti(start_decel_index)


#<<<< This style for all climbing events >>>>>


class AltitudeWhenClimbing(KeyTimeInstanceNode):
    '''
    Creates KTIs at certain altitudes when the aircraft is climbing.
    '''
    NAME_FORMAT = '%(altitude)d Ft Climbing'
    ALTITUDES = [10, 20, 35, 50, 75, 100, 150, 200, 300, 400, 500, 750, 1000,
                 1500, 2000, 2500, 3000, 3500, 4000, 5000, 6000, 7000, 8000, 
                 9000, 10000]
    NAME_VALUES = {'altitude': ALTITUDES}
    
    def derive(self, climbing=S('Climbing'), alt_aal=P('Altitude AAL')):
        alt_array = hysteresis(alt_aal.array, 10)
        for climb in climbing:
            for alt_threshold in self.ALTITUDES:
                # Will trigger a single KTI per height (if threshold is crossed)
                # per climbing phase.
                index = index_at_value(alt_array, climb.slice, alt_threshold)
                if index:
                    self.create_kti(index, altitude=alt_threshold)


class AltitudeWhenDescending(KeyTimeInstanceNode):
    '''
    Creates KTIs at certain heights when the aircraft is descending.
    '''
    NAME_FORMAT = '%(altitude)d Ft Descending'
    ALTITUDES = [10, 20, 35, 50, 75, 100, 150, 200, 300, 400, 500, 750, 1000,
                 1500, 2000, 3000, 3500, 4000, 5000, 6000, 7000, 8000, 9000,
                 10000]
    NAME_VALUES = {'altitude': ALTITUDES}
    
    def derive(self, descending=S('Descending'), alt_aal=P('Altitude AAL')):
        alt_array = hysteresis(alt_aal.array, 10)
        for descend in descending:
            for alt_threshold in self.ALTITUDES:
                # Will trigger a single KTI per height (if threshold is crossed)
                # per climbing phase.
                # Q: This will get the first index where the threshold is
                # crossed. Should we be getting the last?
                index = index_at_value(alt_array, descend.slice, alt_threshold)
                if index:
                    self.create_kti(index, altitude=alt_threshold)


class AltitudeInApproach(KeyTimeInstanceNode):
    '''
    Creates KTIs at certain altitudes when the aircraft is in the approach phase.
    '''
    NAME_FORMAT = '%(altitude)d Ft In Approach'
    ALTITUDES = [1000, 1500, 2000, 3000]
    NAME_VALUES = {'altitude': ALTITUDES}
    
    def derive(self, approaches=S('Approach And Landing'),
               alt_aal=P('Altitude AAL')):
        alt_array = hysteresis(alt_aal.array, 10)
        for approach in approaches:
            for alt_threshold in self.ALTITUDES:
                # Will trigger a single KTI per height (if threshold is crossed)
                # per climbing phase.
                index = index_at_value(alt_array, approach.slice, alt_threshold)
                if index:
                    self.create_kti(index, altitude=alt_threshold)


class AltitudeInFinalApproach(KeyTimeInstanceNode):
    '''
    Creates KTIs at certain heights when the aircraft is in the final approach.
    '''
    NAME_FORMAT = '%(altitude)d Ft In Final Approach'
    ALTITUDES = [100, 500]
    NAME_VALUES = {'altitude': ALTITUDES}
    
    def derive(self, approaches=S('Approach And Landing'),
               alt_aal=P('Altitude AAL')):
        # Attempt to smooth to avoid fluctuations triggering KTIs.
        # Q: Is this required?
        alt_array = hysteresis(alt_aal.array, 10)
        for approach in approaches:
            for alt_threshold in self.ALTITUDES:
                # Will trigger a single KTI per height (if threshold is crossed)
                # per climbing phase.
                index = index_at_value(alt_array, approach.slice, alt_threshold)
                if index:
                    self.create_kti(index, altitude=alt_threshold)


# Climb KTI does not yet exist.
#class AltitudeInClimb(KeyTimeInstanceNode):
    #'''
    #Creates KTIs at certain heights when the aircraft is in the climb phase.
    #'''
    #NAME_FORMAT = '%(height)d Ft In Approach'
    #HEIGHTS = [1000, 1500, 2000, 3500]
    #NAME_VALUES = {'height': HEIGHTS}
    
    #def derive(self, climbs=S('Climb'), alt_aal=P('Altitude AAL')):
        #alt = hysteresis(alt_aal.array, 10)
        #for climb in climbs:
            #for height in self.HEIGHTS:
                ## Will trigger a single KTI per height (if threshold is crossed)
                ## per climbing phase.
                #index = index_at_value(alt, descend.slice, height)
                #if index:
                    #self.create_kti(index, height=height)


class _10FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented

    
class _20FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented

    
class _35FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented

    
class _50FtClimbing(KeyTimeInstanceNode):
    def derive(self, climbing=S('Climbing'), alt_aal=P('Altitude AAL')):
        for climb in climbing:
            index = start_decel_index(alt_aal.array,
                                      climb.index, 50)
            self.create_kti(index, '50 Ft In Initial Climb')


class _75FtClimbing(KeyTimeInstanceNode):
    def derive(self, climbing=S('Climbing'), alt_aal=P('Altitude AAL')):
        for climb in climbing:
            index = start_decel_index(alt_aal.array,
                                          climb.slice, 50)
            self.create_kti(index, '50 Ft In Initial Climb')


class _100FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _150FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _200FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _300FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _400FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _500FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _750FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _1000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _1500FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _2000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _2500FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _3000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _3500FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _4000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _5000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _6000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _7000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _8000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _9000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _10000FtClimbing(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


'''
________Approach and Landing______________________________
'''
class _10000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _9000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _8000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _7000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _6000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _5000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _4000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _3500FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _3000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _2000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _1500FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _1000FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _750FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _500FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _400FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _300FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _200FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _150FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _100FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _75FtDescending(KeyTimeInstanceNode):
    def derive(self, alt_aal=P('Altitude AAL')):
        return NotImplemented


class _50FtToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _35FtDescending(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _20FtDescending(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _10FtDescending(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _5MinToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _4MinToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _3MinToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _2MinToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _90SecToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented


class _1MinToTouchdown(KeyTimeInstanceNode):
    def derive(self, touchdown=KTI('Touchdown')): # Q: Args?
        return NotImplemented




class TriggerPassiveNodes(KeyTimeInstanceNode):
    # This class just lists nodes that have no dependencies and so would
    # otherwise remain inactive.
    def derive(self, ccbod=S('Climb From Bottom Of Descent'),
               cfbod=S('Climb From Bottom Of Descent'),
               fa=S('Final Approach'),
               ile=S('ILS Localizer Established'),
               og=S('On Ground'),
               hat=S('Heading At Takeoff'),
               hal=S('Heading At Landing'),
               halpoa=S('Heading At Low Point On Approach'),
               rodm=S('RateOfDescentMax'),
               f2ftt=S('Flare20FtToTouchdown'),
               ):
        pass
            
