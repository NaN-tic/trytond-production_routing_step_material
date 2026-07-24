# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.

from trytond.pool import Pool

from . import routing, work


def register():
    Pool.register(
        routing.RoutingStep,
        routing.RoutingStepCategory,
        work.Work,
        work.WorkCycle,
        module='production_routing_step_material', type_='model')
