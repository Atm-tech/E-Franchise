import unittest
from datetime import datetime
from app.projections import monthly_projection


class ProjectionTests(unittest.TestCase):
    def target(self, reward_type='PERCENT_OF_SALES'):
        return {'name': 'Monthly', 'sales_amount': 20000, 'reward_type': reward_type,
                'slabs': [{'threshold_amount': 50000, 'reward_percent': 2},
                          {'threshold_amount': 100000, 'reward_percent': 4}]}

    def test_run_rate_and_next_slab_comparison(self):
        result = monthly_projection(30000, 3000, [self.target()], datetime(2026, 10, 10))
        self.assertEqual(result['sales'], 93000)
        self.assertEqual(result['earning'], 10540)
        comparison = result['comparisons'][0]
        self.assertEqual(comparison['eligible_sales'], 62000)
        self.assertEqual(comparison['sales_gap'], 38000)
        self.assertEqual(comparison['slab_earning'], 19000)

    def test_zero_sales_does_not_invent_commission(self):
        target = self.target(); target['sales_amount'] = 0
        result = monthly_projection(0, 0, [target], datetime(2026, 2, 1))
        self.assertEqual(result['earning'], 0)
        self.assertIsNone(result['comparisons'][0]['slab_earning'])

    def test_month_end_and_threshold_reward_basis(self):
        target = self.target('PERCENT_OF_TARGET'); target['sales_amount'] = 120000
        result = monthly_projection(120000, 6000, [target], datetime(2024, 2, 29))
        self.assertEqual(result['sales'], 120000)
        self.assertEqual(result['reward'], 4000)
        self.assertIsNone(result['comparisons'][0]['nearest_slab'])
