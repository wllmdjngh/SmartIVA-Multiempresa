import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Backfill de monto_retenido_archivo/monto_iva_archivo para retenciones
# creadas por cargas del Libro de Ventas ANTES de que estos campos
# existieran (ese código se agregó en 19.0.2.14.11, pero cargas
# confirmadas antes de esa versión nunca los llenaron -- quedaron en 0 por
# defecto). Bug real reportado 2026-08-06 (Cementos, factura C-006337):
# "Facturas con Diferencia" mostraba una diferencia falsa de Bs 149.644,80
# porque el archivo comparaba contra un monto_retenido_archivo=0 que nunca
# se llenó, no porque hubiera una diferencia real (la línea de la carga sí
# tenía el monto correcto guardado). Afectaba 1.308 de 1.326 filas.
#
# UPDATE por SQL directo (join contra ve_conecta_carga_ventas_linea, no
# toca las que ya tienen un valor -- respeta corridas manuales previas),
# luego recompute vía ORM de diferencia_vs_archivo/pct_diferencia_archivo
# (dependen de estos campos, un UPDATE crudo no los recalcula solo).


def migrate(cr, version):
    cr.execute("""
        UPDATE ve_wh_iva w
        SET monto_retenido_archivo = l.monto_retenido,
            monto_iva_archivo = l.monto_iva,
            viene_de_libro_ventas = true
        FROM ve_conecta_carga_ventas_linea l
        WHERE l.invoice_id = w.invoice_id
          AND l.invoice_id IS NOT NULL
          AND w.monto_retenido_archivo = 0
          AND l.monto_retenido != 0
    """)
    tocadas = cr.rowcount

    env = api.Environment(cr, SUPERUSER_ID, {})
    recs = env['ve.wh.iva'].search([('viene_de_libro_ventas', '=', True)])
    recs._compute_diferencia_vs_archivo()

    _logger.info(
        've_retencion_iva 19.0.2.14.17: %d retencion(es) con '
        'monto_retenido_archivo/monto_iva_archivo backfilleados, %d '
        'recalculadas', tocadas, len(recs))
