import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# pct_diferencia_archivo (recreada como varchar) y es_agente_retencion
# (related+store nuevo) quedan en NULL tras el ALTER de pre_migrate.
# es_agente_retencion se backfillea por SQL directo (simple join contra
# res_partner). pct_diferencia_archivo depende de formateo en Python (el
# texto "Archivo=0"), así que se recalcula vía ORM.


def migrate(cr, version):
    cr.execute("""
        UPDATE ve_wh_iva w
        SET es_agente_retencion = p.es_agente_retencion
        FROM res_partner p
        WHERE p.id = w.partner_id
    """)

    env = api.Environment(cr, SUPERUSER_ID, {})
    recs = env['ve.wh.iva'].search([])
    recs._compute_diferencia_vs_archivo()

    _logger.info(
        've_retencion_iva 19.0.2.14.16: es_agente_retencion backfilleado '
        '(%d fila(s)), %d retencion(es) recalculadas (pct_diferencia_archivo)',
        cr.rowcount, len(recs))
