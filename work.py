# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.

from trytond.exceptions import UserError
from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval


class Work(metaclass=PoolMeta):
    __name__ = 'production.work'

    routing_step = fields.Many2One(
        'production.routing.step', 'Routing Step', readonly=True,
        ondelete='SET NULL')
    input_moves = fields.Function(
        fields.Many2Many(
            'stock.move', None, None, 'Input Moves',
            domain=[
                ('production_input', '=', Eval('production', -1)),
                ],
            states={
                'readonly': Eval('state').in_(['finished', 'done']),
                },
            depends=['state', 'production']),
        'get_input_moves')

    def get_input_moves(self, name=None):
        if not self.production or not self.routing_step:
            return []
        moves = [
            move for move in self.production.inputs
            if move.state not in {'done', 'cancelled'}
            ]
        step = self.routing_step
        allowed_category_ids = step._get_allowed_category_ids() or set()
        if not allowed_category_ids:
            return []
        return [
            move for move in moves
            if (move.product
                and {c.id for c in move.product.template.categories}
                & allowed_category_ids)
            ]

    def consume_cycle_ingredients(self, cycle, ingredient_lines=None):
        if not cycle:
            return (
                'Debes tener un ciclo seleccionado o en ejecución para '
                'consumir ingredientes.')

        if ingredient_lines is not None:
            error = self._sync_cycle_ingredient_lines(cycle, ingredient_lines)
            if error:
                return error

        pool = Pool()
        Move = pool.get('stock.move')
        Uom = pool.get('product.uom')
        lot_cache = {}
        to_consume = []
        source_moves = [
            move for move in self.input_moves
            if move.state not in ('done', 'cancelled')
        ]
        remaining_by_move = {
            move.id: (move.quantity or 0.0)
            for move in source_moves
        }
        plans = []
        for line in self._get_pending_cycle_ingredient_lines(cycle):
            try:
                quantity = float(line.quantity or 0)
            except ValueError:
                return 'Hay cantidades no válidas en los ingredientes.'
            if quantity <= 0:
                return 'La cantidad de un ingrediente debe ser mayor que cero.'
            if not line.product:
                return 'Todos los ingredientes deben tener producto.'

            available_moves = [
                move for move in source_moves
                if (move.product == line.product
                    and remaining_by_move.get(move.id, 0.0) > 0)
            ]
            if not available_moves:
                return (
                    'No hay movimientos de entrada disponibles para el '
                    'ingrediente "%s".' % line.product.rec_name)
            remaining_quantity = quantity
            line_plan = []
            for move in available_moves:
                available = remaining_by_move.get(move.id, 0.0)
                if available <= 0:
                    continue
                requested = Uom.compute_qty(
                    line.unit, remaining_quantity, move.unit)
                consume_quantity = min(available, requested)
                line_plan.append((move, consume_quantity))
                remaining_by_move[move.id] = move.unit.round(
                    available - consume_quantity)
                remaining_quantity -= Uom.compute_qty(
                    move.unit, consume_quantity, line.unit, round=False)
                if line.unit.round(remaining_quantity) <= 0:
                    break
            if line.unit.round(remaining_quantity) > 0:
                return (
                    'No hay cantidad suficiente para consumir el ingrediente '
                    '"%s".' % line.product.rec_name)
            plans.append((line, line_plan))

        for move in source_moves:
            final_quantity = remaining_by_move.get(move.id, move.quantity or 0.0)
            if move.quantity != final_quantity:
                move.quantity = final_quantity
                move.save()

        for line, line_plan in plans:
            lot = self._get_cycle_ingredient_lot(line, lot_cache)
            for move, quantity in line_plan:
                new_move, = Move.copy([move], {
                    'origin': cycle,
                    'production_input': self.production,
                    'production_output': None,
                    'quantity': quantity,
                    'lot': lot,
                    'production_work_cycle_ingredient': line.id,
                })
                to_consume.append(new_move)
        Move.do(to_consume)
        return None

    def _sync_cycle_ingredient_lines(self, cycle, ingredient_lines):
        pool = Pool()
        Ingredient = pool.get('production.work.cycle.ingredient')

        consumed_lines = [
            line for line in cycle.ingredient_lines
            if line.has_consumption_moves
        ]
        if consumed_lines:
            return (
                'No se pueden modificar ingredientes que ya tienen consumos '
                'generados.')

        Ingredient.delete(list(cycle.ingredient_lines))
        to_create = []
        for line in ingredient_lines:
            product = line.get('product')
            move_id = line.get('move_id')
            if not product and move_id:
                move = Pool().get('stock.move')(int(move_id))
                product = move.product.id if move.product else None
            if not product:
                continue
            raw_quantity = str(line.get('quantity') or '').strip()
            if raw_quantity == '':
                raw_quantity = '0'
            try:
                quantity = float(raw_quantity)
            except ValueError:
                return 'Hay cantidades no válidas en los ingredientes.'
            lot = None
            raw_lot = line.get('lot')
            if raw_lot:
                if hasattr(raw_lot, 'id'):
                    lot = raw_lot.id
                else:
                    raw_lot = str(raw_lot).strip()
                    if raw_lot:
                        lot = self._get_or_create_product_lot(
                            int(product), raw_lot).id
            to_create.append({
                'cycle': cycle.id,
                'product': int(product),
                'quantity': quantity,
                'lot': lot,
            })
        if to_create:
            Ingredient.create(to_create)
        return None

    def _get_pending_cycle_ingredient_lines(self, cycle):
        return [
            line for line in cycle.ingredient_lines
            if not line.has_consumption_moves
        ]

    def _get_cycle_ingredient_lot(self, line, lot_cache):
        if not line.lot or not line.product:
            return None
        if hasattr(line.lot, 'id') and line.lot.id:
            return line.lot

        pool = Pool()
        Lot = pool.get('stock.lot')
        raw_lot = str(line.lot).strip()
        cache_key = (line.product.id, raw_lot)
        lot = lot_cache.get(cache_key)
        if lot is not None:
            return lot

        lots = Lot.search([
            ('product', '=', line.product.id),
            ('number', '=', raw_lot),
        ], limit=1)
        if lots:
            lot = lots[0]
        else:
            lot = Lot.create([{
                'product': line.product.id,
                'number': raw_lot,
            }])[0]
        lot_cache[cache_key] = lot
        return lot

    def _get_or_create_product_lot(self, product_id, lot_number):
        Lot = Pool().get('stock.lot')
        lots = Lot.search([
            ('product', '=', product_id),
            ('number', '=', lot_number),
        ], limit=1)
        if lots:
            return lots[0]
        lot, = Lot.create([{
            'product': product_id,
            'number': lot_number,
        }])
        return lot

class WorkCycle(metaclass=PoolMeta):
    __name__ = 'production.work.cycle'

    input_moves = fields.One2Many(
            'stock.move', 'origin', 'Input Moves',
            states={
                'readonly': Eval('state').in_(['done', 'cancelled']),
                },
            depends=['state'])
    ingredient_lines = fields.One2Many(
        'production.work.cycle.ingredient', 'cycle', 'Ingredients',
        context={
            'default_cycle': Eval('id', -1),
            'default_work': Eval('work', -1),
        },
        states={
            'readonly': Eval('state').in_(['done', 'cancelled']),
        },
        depends=['id', 'state', 'work'])
    input_products = fields.Function(
        fields.Many2Many(
            'product.product', None, None, 'Input Products',
            context={
                'company': Eval('company', -1),
            },
            depends=['company']),
        'get_input_products')

    @classmethod
    def do(cls, cycles):
        for cycle in cycles:
            error = cycle.work.consume_cycle_ingredients(cycle)
            if error:
                raise UserError(error)
        super().do(cycles)

    def get_input_products(self, name):
        if not self.work:
            return []
        product_ids = []
        for move in self.work.get_input_moves():
            if move.product and move.product.id not in product_ids:
                product_ids.append(move.product.id)
        return product_ids

class WorkCycleIngredient(ModelSQL, ModelView):
    'Production Work Cycle Ingredient'
    __name__ = 'production.work.cycle.ingredient'

    cycle = fields.Many2One(
        'production.work.cycle', 'Cycle', required=True, ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', 'Product', required=True,
        domain=[(
            'id', 'in',
            Eval('_parent_cycle', Eval('context', {})).get('input_products', []),
        )],
        depends=['cycle'])
    quantity = fields.Float(
        'Quantity', required=True,
        digits=(16, Eval('unit_digits', 2)),
        depends=['unit_digits'])
    unit = fields.Function(
        fields.Many2One('product.uom', 'Unit'),
        'on_change_with_unit')
    unit_digits = fields.Function(
        fields.Integer('Unit Digits'),
        'on_change_with_unit_digits')
    lot = fields.Many2One(
        'stock.lot', 'Lot', ondelete='RESTRICT',
        domain=[
            ('product', '=', Eval('product', -1)),
            ('id', 'in', Eval('valid_lots', [])),
        ],
        depends=['product', 'valid_lots'])
    valid_lots = fields.Function(
        fields.Many2Many('stock.lot', None, None, 'Valid Lots'),
        'get_valid_lots')
    consumption_moves = fields.One2Many(
        'stock.move', 'production_work_cycle_ingredient', 'Consumption Moves',
        readonly=True)
    has_consumption_moves = fields.Function(
        fields.Boolean('Has Consumption Moves'),
        'get_has_consumption_moves')

    @fields.depends('product')
    def on_change_with_unit(self, name=None):
        if self.product and self.product.default_uom:
            return self.product.default_uom.id

    @fields.depends('product')
    def on_change_with_unit_digits(self, name=None):
        if self.product and self.product.default_uom:
            return self.product.default_uom.digits
        return 2

    @fields.depends('product', 'lot')
    def on_change_product(self):
        if self.lot and self.product and self.lot.product != self.product:
            self.lot = None

    def get_valid_lots(self, name):
        if not self.product:
            return []
        pool = Pool()
        cycle = self.cycle
        work = None
        if not cycle:
            cycle_id = self._context.get('default_cycle')
            if cycle_id:
                Cycle = pool.get('production.work.cycle')
                cycle = Cycle(cycle_id)
        if cycle and cycle.work:
            work = cycle.work
        if not work:
            work_id = self._context.get('default_work')
            if work_id:
                Work = pool.get('production.work')
                work = Work(work_id)
        if not work:
            return []
        lot_ids = []
        for move in work.get_input_moves():
            if move.product != self.product or not move.lot:
                continue
            if move.state in ('done', 'cancelled'):
                continue
            if move.lot.id not in lot_ids:
                lot_ids.append(move.lot.id)
        if self.lot and self.lot.id not in lot_ids:
            lot_ids.append(self.lot.id)
        return lot_ids

    def get_has_consumption_moves(self, name):
        return any(
            move.state not in ('cancelled',)
            for move in self.consumption_moves or [])



class Move(metaclass=PoolMeta):
    __name__ = 'stock.move'

    production_work_cycle_ingredient = fields.Many2One(
        'production.work.cycle.ingredient', 'Cycle Ingredient',
        readonly=True, ondelete='RESTRICT')
