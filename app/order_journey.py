"""Display the full fulfilment path while retaining actual event timestamps."""


def build_order_journey(order, events, *, require_seal=True, require_docket=False):
    pickup = order['fulfillment_mode'] == 'FRANCHISE_PICKUP'
    steps = [
        ('EXECUTED', 'Order confirmed and sent to outlet'),
        ('BILLED', 'Outlet bill created'),
        ('PACKED_INDIVIDUALLY', 'Order packed at outlet'),
    ]
    statuses = {event['status'] for event in events}
    if require_docket or 'DOCKET_CREATED' in statuses:
        steps.append(('DOCKET_CREATED', 'Grouped into a dispatch docket'))
    steps += [('HANDED_TO_GUARD', 'Sent to guard desk'), ('AT_GUARD_DESK', 'Received by guard')]
    if require_seal or 'SEAL_CHECK' in statuses:
        steps.append(('SEAL_CHECK', 'Seal inspection recorded'))
    steps.append(('WEIGHT_CHECK_PASSED', 'Weight check passed'))
    if pickup:
        steps.append(('PICKED_BY_FRANCHISEE', 'Collected by franchise - customer handover pending'))
    else:
        steps += [('DELIVERY_ASSIGNED', 'Delivery partner assigned'),
                  ('ACCEPTED', 'Delivery partner accepted the assignment'),
                  ('PICKED_UP', 'Delivery partner collected the parcel'),
                  ('OUT_FOR_DELIVERY', 'Delivery partner is on the way'),
                  ('DELIVERED', 'Collected by franchise - customer handover pending')]
    steps.append(('CUSTOMER_DELIVERED', 'Delivered to customer'))
    positions = {status: index for index, (status, _) in enumerate(steps)}
    aliases = {'DELIVERED': 'PICKED_BY_FRANCHISEE'} if pickup else {
        'ASSIGNED': 'DELIVERY_ASSIGNED', 'PICKED_BY_DELIVERY': 'PICKED_UP'}
    observed = {}
    furthest = -1
    attention = None
    for event in events:
        status = aliases.get(event['status'], event['status'])
        if status in positions:
            observed[status] = event
            furthest = max(furthest, positions[status])
        if event['status'] == 'WEIGHT_CHECK_ALERT':
            attention = ('WEIGHT_CHECK_PASSED', event, 'Weight needs recheck')
        elif event['status'] == 'SEAL_CHECK' and event.get('notes') == 'BROKEN':
            attention = ('SEAL_CHECK', event, 'Seal needs recheck')
        elif status in positions:
            attention = None
    # Current order status is a fallback for older orders with incomplete event history.
    current = aliases.get(order['status'], order['status'])
    if current in positions:
        furthest = max(furthest, positions[current])
    if attention and positions[attention[0]] >= furthest:
        furthest = positions[attention[0]] - 1
    else:
        attention = None
    journey = []
    if events and events[-1]['status'] == 'ON_HOLD':
        journey.append(dict(events[-1], title='Waiting for wallet balance', completed=False,
                            current=True, attention=True))
        furthest = -1
    for index, (status, title) in enumerate(steps):
        event = observed.get(status, {})
        is_attention = bool(attention and attention[0] == status)
        journey.append({'status': status, 'title': attention[2] if is_attention else title,
                        'created_at': (attention[1] if is_attention else event).get('created_at', ''),
                        'completed': index <= furthest, 'current': index == furthest,
                        'attention': is_attention})
    if events and events[-1]['status'] in {'CANCELLED', 'DELIVERY_FAILED', 'FAILED', 'RETURN_RECORDED', 'REOPEN', 'REASSIGN'}:
        for step in journey:
            step['current'] = False
        journey.append(dict(events[-1], completed=False, current=True, attention=True))
    for index, step in enumerate(journey):
        step['connector_completed'] = bool(step['completed'] and index + 1 < len(journey)
                                            and journey[index + 1]['completed'])
    return journey
