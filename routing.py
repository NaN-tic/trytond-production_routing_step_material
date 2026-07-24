# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.

from trytond.model import ModelSQL, fields
from trytond.pool import Pool, PoolMeta


class RoutingStep(metaclass=PoolMeta):
    __name__ = 'production.routing.step'

    consume_product_categories = fields.Many2Many(
        'production.routing.step-product.category', 'step', 'category',
        'Consume Product Categories')

    def get_work(self, production, work_center_picker):
        work = super().get_work(production, work_center_picker)
        work.routing_step = self
        return work

    def _get_allowed_category_ids(self):
        if not self.consume_product_categories:
            return None
        Category = Pool().get('product.category')
        selected_ids = [c.id for c in self.consume_product_categories if c.id]
        if not selected_ids:
            return None
        # Expand selected categories with descendants without relying on
        # 'child_of' over 'id' because it is not supported by all backends.
        allowed_ids = set(selected_ids)
        frontier = set(selected_ids)
        while frontier:
            children = Category.search([
                    ('parent', 'in', list(frontier)),
                    ])
            new_ids = {c.id for c in children} - allowed_ids
            if not new_ids:
                break
            allowed_ids.update(new_ids)
            frontier = new_ids
        return allowed_ids


class RoutingStepCategory(ModelSQL):
    'Routing Step - Product Category'
    __name__ = 'production.routing.step-product.category'
    step = fields.Many2One(
        'production.routing.step', 'Routing Step',
        required=True, ondelete='CASCADE')
    category = fields.Many2One(
        'product.category', 'Category',
        required=True, ondelete='CASCADE')
