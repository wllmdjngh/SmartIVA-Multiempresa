import logging

_logger = logging.getLogger(__name__)

# Campos nuevos ve.wh.iva para la vista dedicada "Facturas con Diferencia"
# (ve_wh_iva_view_list_diferencias_archivo): pct_diferencia_archivo,
# monto_iva_archivo, monto_iva_total, base_imponible_total. Ver
# models/ve_wh_iva.py y models/ve_conecta_carga_ventas.py::action_confirmar.
# Pedido explícito 2026-08-05.


def migrate(cr, version):
    for columna in ('pct_diferencia_archivo', 'monto_iva_archivo',
                     'monto_iva_total', 'base_imponible_total'):
        cr.execute(f"""
            ALTER TABLE ve_wh_iva ADD COLUMN IF NOT EXISTS {columna} numeric
        """)
    _logger.info(
        've_retencion_iva 19.0.2.14.13: columnas pct_diferencia_archivo, '
        'monto_iva_archivo, monto_iva_total, base_imponible_total '
        'aseguradas en ve_wh_iva')
