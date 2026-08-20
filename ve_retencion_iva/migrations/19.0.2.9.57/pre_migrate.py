import logging

_logger = logging.getLogger(__name__)

# Validación de montos OCR en el Buzón de Comprobantes: agrega la columna
# monto_esperado a ve_comprobante_inbox (nuevo estado 'con_diferencias' del
# campo estado no requiere migración, es solo un valor nuevo de selection).


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_comprobante_inbox
        ADD COLUMN IF NOT EXISTS monto_esperado double precision DEFAULT 0.0;
    """)
    _logger.info(
        've_retencion_iva 19.0.2.9.57: columna monto_esperado agregada a ve_comprobante_inbox')
