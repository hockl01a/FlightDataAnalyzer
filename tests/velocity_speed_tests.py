#!/usr/bin/env python
#coding:utf-8

import inspect
import unittest

from analysis_engine import velocity_speed
from analysis_engine.velocity_speed import VelocitySpeed

class TestVelocitySpeed(unittest.TestCase):
    def setUp(self):
        self.velocity_speed = VelocitySpeed()
        self.velocity_speed.v2_table = {
            'weight': (100, 110, 120, 130, 140, 150, 160, 170, 180, 190),
                   5: (127, 134, 139, 145, 151, 156, 161, 166, 171, 176),
                  15: (122, 128, 134, 139, 144, 149, 154, 159, 164, 168),
                  20: (118, 124, 129, 134, 140, 144, 149, 154, 159, 164),
        }
        self.velocity_speed.airspeed_reference_table = {
            'weight': (100, 110, 120, 130, 140, 150, 160, 170, 180, 190),
                   5: (114, 121, 128, 134, 141, 147, 153, 158, 164, 169),
                  15: (109, 116, 122, 129, 141, 135, 146, 151, 157, 162),
                  20: (105, 111, 118, 124, 130, 135, 141, 147, 152, 158),
        }

    def test_v2(self):
        self.velocity_speed.interpolate = False
        self.assertEquals(self.velocity_speed.v2(119000, 20), 129)
        self.assertEquals(self.velocity_speed.v2(120000, 20), 129)
        self.assertEquals(self.velocity_speed.v2(121000, 20), 134)
        self.assertRaises(KeyError, self.velocity_speed.v2, 165000, 14)

    def test_v2_interpolated(self):
        self.velocity_speed.interpolate = True
        self.assertEquals(self.velocity_speed.v2(145000, 20), 142)
        self.assertEquals(self.velocity_speed.v2(120000, 20), 129)
        self.assertEquals(self.velocity_speed.v2(165000, 5), 163.5)
        self.assertEquals(self.velocity_speed.v2(94000, 20), None)
        self.assertRaises(KeyError, self.velocity_speed.v2, 165000, 14)

    def test_v2_minimum(self):
        self.velocity_speed.interpolate = True
        self.velocity_speed.minimum_speed = 125
        self.assertEquals(self.velocity_speed.v2(100500, 15), 125)
        self.velocity_speed.interpolate = False
        self.assertEquals(self.velocity_speed.v2(100500, 15), 128)
        self.assertRaises(KeyError, self.velocity_speed.v2, 165000, 14)

    def test_airspeed_reference(self):
        self.velocity_speed.minimum_speed = False
        self.assertEquals(self.velocity_speed.airspeed_reference(119000, 15), 122)
        self.assertEquals(self.velocity_speed.airspeed_reference(120000, 15), 122)
        self.assertEquals(self.velocity_speed.airspeed_reference(121000, 15), 129)
        self.assertRaises(KeyError, self.velocity_speed.airspeed_reference, 121000, 14)

    def test_airspeed_reference_interpolated(self):
        self.velocity_speed.interpolate = True
        self.assertEquals(self.velocity_speed.airspeed_reference(120000, 5), 128)
        self.assertEquals(self.velocity_speed.airspeed_reference(120000, 15), 122)
        self.assertEquals(self.velocity_speed.airspeed_reference(145000, 20), 132.5)
        self.assertEquals(self.velocity_speed.airspeed_reference(94000, 20), None)
        self.assertRaises(KeyError, self.velocity_speed.airspeed_reference, 165000, 14)

    def test_airspeed_reference_minimum(self):
        self.velocity_speed.interpolate = True
        self.velocity_speed.minimum_speed = 115
        self.assertEquals(self.velocity_speed.airspeed_reference(100500, 15), 115)
        self.velocity_speed.interpolate = False
        self.assertEquals(self.velocity_speed.airspeed_reference(100500, 15), 116)
        self.assertRaises(KeyError, self.velocity_speed.airspeed_reference, 165000, 14)


def _add_tests(generator):
    def class_decorator(cls):
        """Add tests to `cls` generated by `generator()`."""
        for f, table_name, table_class in generator():
            test = lambda self, tn=table_name, tc=table_class, f=f: f(self, tn, tc)
            test.__name__ = 'test_vspeed_table_%s' % table_name
            setattr(cls, test.__name__, test)
        return cls
    return class_decorator

def _test_pairs():
    def t(self, table_name, table_class):
        self.assertTrue('weight' in table_class.v2_table,
                        'Weight not in V2 lookup table %s' % table_name)
        self.assertTrue(len(table_class.v2_table) > 1,
                        'No rows in V2 lookup table %s' % table_name)
        self.assertTrue('weight' in table_class.airspeed_reference_table,
           'Weight not in Vref lookup table %s' % table_name)
        self.assertTrue(len(table_class.airspeed_reference_table) > 1,
                        'No rows in Vref lookup table %s' % table_name)

        v2_table_columns = len(table_class.v2_table['weight'])
        ref_table_columns = len(table_class.airspeed_reference_table['weight'])

        v2_equal_len_rows = [len(row) == v2_table_columns for row in table_class.v2_table.values()]
        ref_equal_len_rows = [len(row) == ref_table_columns for row in table_class.airspeed_reference_table.values()]

        self.assertTrue(all(v2_equal_len_rows),
                        'Differing number of entries in each row of v2 table')
        self.assertTrue(all(ref_equal_len_rows),
                        'Differing number of entries in each row of reference table')

    vspeed_classes = inspect.getmembers(velocity_speed, lambda mod: inspect.isclass(mod) and issubclass(mod, VelocitySpeed) and mod.__name__ != 'VelocitySpeed')
    for table_name, table_class in vspeed_classes:
        yield t, table_name, table_class

class TestVelocitySpeedTables(unittest.TestCase):
    pass
TestVelocitySpeedTables = _add_tests(_test_pairs)(TestVelocitySpeedTables)
