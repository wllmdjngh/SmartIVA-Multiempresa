import logging

_logger = logging.getLogger(__name__)

# Campos nuevos ve.wh.iva.monto_retenido_archivo (Float, guarda el monto tal
# cual venía en el Libro de Ventas al crear la retención) y
# diferencia_vs_archivo (Float, compute+store) — ver models/ve_wh_iva.py y
# models/ve_conecta_carga_ventas.py::action_ver_diferencias_archivo.
# Pedido explícito 2026-08-05.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS monto_retenido_archivo numeric
    """)
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS diferencia_vs_archivo numeric
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.11: columnas monto_retenido_archivo y '
        'diferencia_vs_archivo aseguradas en ve_wh_iva')
