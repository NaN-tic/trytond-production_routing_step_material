# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.

from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If


class RoutingStep(metaclass=PoolMeta):
    __name__ = 'production.routing.step'

    consume_product_categories = fields.Many2Many(
        'production.routing.step-product.category', 'step', 'category',
        'Consume Product Categories')
    explode_product = fields.Many2One(
        'product.product', 'BOM Product',
        domain=[
            ('producible', '=', True),
            ])
    explode_bom = fields.Many2One(
        'production.bom', 'BOM',
        domain=[
            ('output_products', '=', Eval('explode_product', 0)),
            ],
        depends=['explode_product'])
    explode_uom_category = fields.Function(
        fields.Many2One('product.uom.category', 'BOM UOM Category'),
        'on_change_with_explode_uom_category')
    explode_unit = fields.Many2One(
        'product.uom', 'BOM Unit',
        domain=[
            ('category', '=', Eval('explode_uom_category')),
            ],
        depends=['explode_uom_category'])
    explode_quantity = fields.Float('BOM Quantity')
    materials = fields.One2Many(
        'production.routing.step.material', 'step', 'Consumed Materials')
    output_materials = fields.One2Many(
        'production.routing.step.output_material', 'step', 'Output Materials')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'explode_from_bom': {},
                })

    @classmethod
    def view_attributes(cls):
        invisible = Bool(Eval('consume_product_categories', []))
        return super().view_attributes() + [
            ('/form/group[@id="explode_bom_block"]', 'states', {
                    'invisible': invisible,
                    }),
            ('/form/group[@id="consumed_materials_block"]', 'states', {
                    'invisible': invisible,
                    }),
            ]

    def get_work(self, production, work_center_picker):
        work = super().get_work(production, work_center_picker)
        work.routing_step = self
        return work

    @staticmethod
    def default_explode_quantity():
        return 1

    @fields.depends('explode_product', 'explode_bom', 'explode_unit')
    def on_change_explode_product(self):
        if self.explode_product:
            self.explode_unit = self.explode_product.default_uom
            if self.explode_product.boms:
                self.explode_bom = self.explode_product.boms[0].bom
        else:
            self.explode_bom = None
            self.explode_unit = None
        self._set_explode_quantity_from_bom()
        self._explode_from_bom()

    @fields.depends('explode_product')
    def on_change_with_explode_uom_category(self, name=None):
        if self.explode_product:
            return self.explode_product.default_uom.category.id

    @fields.depends('explode_product', 'explode_bom', 'explode_unit',
        'explode_quantity', methods=['_explode_from_bom'])
    def on_change_explode_bom(self):
        self._set_explode_quantity_from_bom()
        self._explode_from_bom()

    @fields.depends('explode_product', 'explode_bom', 'explode_unit',
        'explode_quantity', methods=['_explode_from_bom'])
    def on_change_explode_unit(self):
        self._set_explode_quantity_from_bom()
        self._explode_from_bom()

    @fields.depends('explode_product', 'explode_bom', 'explode_unit',
        'explode_quantity', methods=['_explode_from_bom'])
    def on_change_explode_quantity(self):
        self._explode_from_bom()

    @ModelView.button_change(methods=['_explode_from_bom'])
    def explode_from_bom(self):
        self._explode_from_bom()

    def _explode_from_bom(self):
        pool = Pool()
        StepMaterial = pool.get('production.routing.step.material')
        StepOutputMaterial = pool.get('production.routing.step.output_material')

        self.materials = []
        self.output_materials = []
        if not (self.explode_product and self.explode_bom and self.explode_unit):
            return

        quantity = self.explode_quantity or 0
        factor = self.explode_bom.compute_factor(
            self.explode_product, quantity, self.explode_unit)
        unit_quantity = quantity if quantity > 0 else 1
        allowed_category_ids = self._get_allowed_category_ids()

        materials = []
        for input_ in self.explode_bom.inputs:
            if (allowed_category_ids is not None
                    and not {c.id for c in input_.product.template.categories}
                    & allowed_category_ids):
                continue
            material = StepMaterial()
            material.product = input_.product
            material.unit = input_.unit
            material.quantity_type = 'ratio'
            material.quantity = input_.compute_quantity(factor) / unit_quantity
            materials.append(material)
        self.materials = materials

        outputs = []
        for output in self.explode_bom.outputs:
            output_material = StepOutputMaterial()
            output_material.product = output.product
            output_material.unit = output.unit
            output_material.quantity_type = 'ratio'
            output_material.quantity = output.compute_quantity(factor) / unit_quantity
            outputs.append(output_material)
        self.output_materials = outputs

    def _set_explode_quantity_from_bom(self):
        if not (self.explode_product and self.explode_bom and self.explode_unit):
            return
        Uom = Pool().get('product.uom')
        quantity = 0
        for output in self.explode_bom.outputs:
            if output.product != self.explode_product:
                continue
            quantity += Uom.compute_qty(
                output.unit, output.quantity, self.explode_unit, round=False)
        if quantity > 0:
            self.explode_quantity = quantity

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


class RoutingStepMaterial(ModelSQL, ModelView):
    'Routing Step Material'
    __name__ = 'production.routing.step.material'

    step = fields.Many2One(
        'production.routing.step', 'Routing Step', required=True,
        ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', 'Product', required=True,
        domain=[
            ('type', 'in', ['goods', 'assets']),
            If(Bool(Eval('step_allowed_categories', [])),
                ('template.categories', 'in', Eval('step_allowed_categories', [])),
                ()),
            ],
        depends=['step_allowed_categories'],
        )
    quantity_type = fields.Selection([
            ('fixed', 'Fixed'),
            ('ratio', 'Proportional'),
            ], 'Quantity Type', required=True)
    quantity = fields.Float('Quantity', required=True, digits='unit')
    unit = fields.Many2One(
        'product.uom', 'Unit', required=True,
        domain=[
            ('category', '=', Eval('product_uom_category', -1)),
            ],
        depends=['product_uom_category'])
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', 'Product UOM Category'),
        'on_change_with_product_uom_category')
    step_allowed_categories = fields.Function(
        fields.Many2Many(
            'product.category', None, None, 'Step Allowed Categories'),
        'on_change_with_step_allowed_categories')

    @staticmethod
    def default_quantity():
        return 1

    @staticmethod
    def default_quantity_type():
        return 'fixed'

    @fields.depends('product', '_parent_product.default_uom')
    def on_change_product(self):
        if self.product:
            self.unit = self.product.default_uom

    @fields.depends('product', '_parent_product.default_uom_category')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('step', '_parent_step.consume_product_categories')
    def on_change_with_step_allowed_categories(self, name=None):
        if self.step:
            return list(self.step._get_allowed_category_ids() or [])


class RoutingStepCategory(ModelSQL):
    'Routing Step - Product Category'
    __name__ = 'production.routing.step-product.category'
    step = fields.Many2One(
        'production.routing.step', 'Routing Step',
        required=True, ondelete='CASCADE')
    category = fields.Many2One(
        'product.category', 'Category',
        required=True, ondelete='CASCADE')


class RoutingStepOutputMaterial(ModelSQL, ModelView):
    'Routing Step Output Material'
    __name__ = 'production.routing.step.output_material'

    step = fields.Many2One(
        'production.routing.step', 'Routing Step', required=True,
        ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', 'Product', required=True,
        domain=[
            ('type', 'in', ['goods', 'assets']),
            ],
        )
    quantity_type = fields.Selection([
            ('fixed', 'Fixed'),
            ('ratio', 'Proportional'),
            ], 'Quantity Type', required=True)
    quantity = fields.Float('Quantity', required=True, digits='unit')
    unit = fields.Many2One(
        'product.uom', 'Unit', required=True,
        domain=[
            ('category', '=', Eval('product_uom_category', -1)),
            ],
        depends=['product_uom_category'])
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', 'Product UOM Category'),
        'on_change_with_product_uom_category')

    @staticmethod
    def default_quantity():
        return 1

    @staticmethod
    def default_quantity_type():
        return 'fixed'

    @fields.depends('product', '_parent_product.default_uom')
    def on_change_product(self):
        if self.product:
            self.unit = self.product.default_uom

    @fields.depends('product', '_parent_product.default_uom_category')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id
