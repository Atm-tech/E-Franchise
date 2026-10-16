import unittest

from app.order_journey import build_order_journey


class OrderJourneyTests(unittest.TestCase):
    def journey(self, statuses, mode='FRANCHISE_PICKUP', **kwargs):
        events = [{'status': status, 'title': status, 'created_at': '2026-10-16 13:00:00', 'notes': ''}
                  for status in statuses]
        return build_order_journey({'status': statuses[-1], 'fulfillment_mode': mode}, events, **kwargs)

    def test_confirmed_order_shows_all_future_steps_without_filling_them(self):
        journey = self.journey(['EXECUTED'])
        self.assertEqual(journey[0]['status'], 'EXECUTED')
        self.assertTrue(journey[0]['completed'])
        self.assertFalse(journey[0]['connector_completed'])
        self.assertTrue(all(not step['completed'] for step in journey[1:]))
        self.assertEqual(journey[-2]['status'], 'PICKED_BY_FRANCHISEE')
        self.assertEqual(journey[-1]['status'], 'CUSTOMER_DELIVERED')
        self.assertTrue(all(not step['created_at'] for step in journey[1:]))

    def test_progress_fills_only_through_current_stage(self):
        journey = self.journey(['EXECUTED', 'BILLED', 'PACKED_INDIVIDUALLY'])
        self.assertTrue(all(step['completed'] for step in journey[:3]))
        self.assertTrue(journey[1]['connector_completed'])
        self.assertFalse(journey[2]['connector_completed'])
        self.assertTrue(journey[2]['current'])
        self.assertTrue(all(not step['completed'] for step in journey[3:]))

    def test_pickup_completion_has_one_final_step(self):
        journey = self.journey(['EXECUTED', 'PICKED_BY_FRANCHISEE', 'DELIVERED'])
        self.assertTrue(all(step['completed'] for step in journey[:-1]))
        self.assertFalse(journey[-1]['completed'])
        self.assertEqual(journey[-2]['title'], 'Collected by franchise - customer handover pending')
        self.assertFalse(any('order complete' in step['title'] for step in journey))

    def test_customer_delivery_is_separate_from_collection(self):
        journey = self.journey(['EXECUTED', 'DELIVERED', 'CUSTOMER_DELIVERED'])
        self.assertTrue(journey[-1]['completed'])
        self.assertTrue(journey[-1]['current'])
        self.assertEqual(journey[-1]['title'], 'Delivered to customer')

    def test_delivery_path_and_required_docket_are_visible_in_advance(self):
        journey = self.journey(['EXECUTED'], 'DELIVERY_MAN', require_docket=True)
        statuses = [step['status'] for step in journey]
        for status in ['DOCKET_CREATED', 'DELIVERY_ASSIGNED', 'ACCEPTED', 'PICKED_UP', 'OUT_FOR_DELIVERY', 'DELIVERED']:
            self.assertIn(status, statuses)
        self.assertNotIn('PICKED_BY_FRANCHISEE', statuses)

    def test_hold_cancel_and_weight_alert_do_not_complete_future_steps(self):
        for status in ['ON_HOLD', 'CANCELLED']:
            self.assertFalse(any(step['completed'] for step in self.journey([status])))
        journey = self.journey(['EXECUTED', 'WEIGHT_CHECK_ALERT'])
        weight = next(step for step in journey if step['status'] == 'WEIGHT_CHECK_PASSED')
        self.assertTrue(weight['attention'])
        self.assertFalse(weight['completed'])
        self.assertFalse(journey[-1]['completed'])
