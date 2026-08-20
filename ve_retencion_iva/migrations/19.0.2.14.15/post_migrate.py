import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Backfill de viene_de_libro_ventas=True para retenciones ya creadas por
# cargas del Libro de Ventas ANTES de que existiera este campo (bug real
# encontrado 2026-08-06: "Facturas con Diferencia" mostraba Diferencia=0
# para filas donde el archivo sí traía 0 retenido, porque el compute no
# podía distinguir "archivo=0 real" de "nunca vino de una carga" con solo
# el Float en 0 -- ver ve_wh_iva.py::_compute_diferencia_vs_archivo).
#
# Se identifican por join contra ve_conecta_carga_ventas_linea.invoice_id
# -- toda retención cuya factura pasó por esa carga vino de ahí, sin
# importar si el archivo traía monto o 0. Corre en post_migrate (no
# pre_migrate) porque necesita que la columna ya exista via el sync normal
# de campos del ORM.
#
# write() vía ORM (no UPDATE crudo) a propósito -- dispara el compute de
# diferencia_vs_archivo/pct_diferencia_archivo (dependen de
# viene_de_libro_ventas) de una vez, sin dejarlos guardados pero stale.


def migrate(cr, version):
    cr.execute("""
        SELECT w.id
        FROM ve_wh_iva w
        JOIN ve_conecta_carga_ventas_linea l ON l.invoice_id = w.invoice_id
        WHERE l.invoice_id IS NOT NULL
          AND w.viene_de_libro_ventas IS NOT TRUE
    """)
    ids = [r[0] for r in cr.fetchall()]
    if not ids:
        _logger.info('ve_retencion_iva 19.0.2.14.15: nada que marcar')
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ve.wh.iva'].browse(ids).write({'viene_de_libro_ventas': True})

    _logger.info(
        've_retencion_iva 19.0.2.14.15: %d retencion(es) marcadas '
        'viene_de_libro_ventas=true y recalculadas', len(ids))
