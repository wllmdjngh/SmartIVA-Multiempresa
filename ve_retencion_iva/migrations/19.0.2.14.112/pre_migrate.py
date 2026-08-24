import logging

_logger = logging.getLogger(__name__)

# Campos nuevos account_move.nro_factura / ve_wh_iva.nro_factura -- ver
# models/account_move.py y models/ve_wh_iva.py. Pedido explícito del cliente
# 2026-08-21: la retención al 100% por falta de N° Control (PA
# SNAT/2025/000054) solo debe aplicar si TAMPOCO hay N° Factura -- antes
# faltaba un campo propio para distinguir esto (no confundir con
# ve_wh_iva.nro_documento, que es el N° de Factura del comprobante físico
# OCR, otro concepto).


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE account_move
        ADD COLUMN IF NOT EXISTS nro_factura varchar
    """)
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS nro_factura varchar
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.112: columna nro_factura asegurada en '
        'account_move y ve_wh_iva')
