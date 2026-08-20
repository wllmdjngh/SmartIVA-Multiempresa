import logging

_logger = logging.getLogger(__name__)

# Cruce cobranza vs. comprobante: agrega payment_state (related storeado a
# invoice_id.payment_state) y estado_cobranza (computed storeado) a ve_wh_iva.
# Nuevo menú "Cobranza vs. Comprobante", independiente del Dashboard IVA.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS payment_state varchar;
    """)
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS estado_cobranza varchar;
    """)
    _logger.info(
        've_retencion_iva 19.0.2.9.58: columnas payment_state y estado_cobranza agregadas a ve_wh_iva')
