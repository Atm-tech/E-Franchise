"""Read-only month-end estimates; never create wallet credits."""
import calendar
from datetime import datetime


def monthly_projection(sales, commission, targets, current: datetime):
    days = calendar.monthrange(current.year, current.month)[1]
    factor = days / current.day
    projected_sales = round(sales * factor, 2)
    projected_commission = round(commission * factor, 2)
    reward_total = 0.0
    comparisons = []
    for target in targets:
        eligible = round(target['sales_amount'] * factor, 2)
        slabs = sorted(target['slabs'], key=lambda slab: slab['threshold_amount'])
        achieved = next((slab for slab in reversed(slabs) if eligible >= slab['threshold_amount']), None)
        def reward(amount, slab):
            if slab is None:
                return 0.0
            basis = amount if target['reward_type'] == 'PERCENT_OF_SALES' else slab['threshold_amount']
            return round(basis * slab['reward_percent'] / 100, 2)
        projected_reward = reward(eligible, achieved)
        reward_total += projected_reward
        nearest = next((slab for slab in slabs if slab['threshold_amount'] >= eligible), None)
        comparisons.append({
            'name': target['name'], 'eligible_sales': eligible,
            'projected_reward': projected_reward, 'nearest_slab': nearest,
            'sales_gap': round(max(0, nearest['threshold_amount'] - eligible), 2) if nearest else 0,
            'slab_reward': reward(nearest['threshold_amount'], nearest) if nearest else None,
            'projected_earning': round(projected_commission + projected_reward, 2),
            'slab_earning': round(projected_commission * nearest['threshold_amount'] / eligible
                                  + reward(nearest['threshold_amount'], nearest), 2)
                            if nearest and eligible > 0 else None,
        })
    return {
        'sales': projected_sales, 'commission': projected_commission,
        'reward': round(reward_total, 2),
        'earning': round(projected_commission + reward_total, 2),
        'elapsed_days': current.day, 'month_days': days, 'comparisons': comparisons,
        'has_sales': sales > 0,
    }
