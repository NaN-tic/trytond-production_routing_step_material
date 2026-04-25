# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.

from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval


class Work(metaclass=PoolMeta):
    __name__ = 'production.work'

    routing_step = fields.Many2One(
        'production.routing.step', 'Routing Step', readonly=True,
        ondelete='SET NULL')
    input_moves = fields.Function(
        fields.One2Many(
            'stock.move', None, 'Input Moves',
            domain=[
                ('production_input', '=', Eval('production', -1)),
                ],
            states={
                'readonly': Eval('state').in_(['finished', 'done']),
                },
            depends=['state', 'production']),
        'get_input_moves')

    def get_input_moves(self, name):
        if not self.production or not self.routing_step:
            return []
        moves = [
            move for move in self.production.inputs
            if move.state not in {'done', 'cancelled'}
            ]
        step = self.routing_step
        step_products = {
            material.product.id
            for material in step.materials
            if material.product
            }
        if step_products:
            return [
                move.id for move in moves
                if move.product and move.product.id in step_products
                ]
        allowed_category_ids = step._get_allowed_category_ids() or set()
        if not allowed_category_ids:
            return []
        return [
            move.id for move in moves
            if (move.product
                and {c.id for c in move.product.template.categories}
                & allowed_category_ids)
            ]

class WorkCycle(metaclass=PoolMeta):
    __name__ = 'production.work.cycle'

    input_moves = fields.Function(
        fields.One2Many(
            'stock.move', None, 'Input Moves',
            states={
                'readonly': Eval('state').in_(['done', 'cancelled']),
                },
            depends=['state']),
        'get_input_moves')

    def get_input_moves(self, name):
        if not self.work:
            return []
        return self.work.get_input_moves(name)
