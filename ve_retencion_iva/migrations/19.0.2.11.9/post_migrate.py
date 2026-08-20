import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # pre_migrate.py cambió `state` por SQL crudo para las filas que aún
    # tenían 'conciliado'/'declarado' — el SQL crudo no dispara los compute
    # de Odoo, así que estado_recepcion y fuera_plazo (store=True, ambos
    # dependen de `state`) quedan con el valor viejo hasta forzar el
    # recompute aquí, para toda la tabla (barato, no hay volumen que lo
    # justifique filtrar).
    env = api.Environment(cr, SUPERUSER_ID, {})
    records = env['ve.wh.iva'].search([])
    records._compute_estado_recepcion()
    records._compute_fuera_plazo()
    env.flush_all()
    _logger.info(
        've_retencion_iva %s (Etapa 4): estado_recepcion/fuera_plazo '
        'recalculados para %d retención(es)',
        version, len(records),
    )
