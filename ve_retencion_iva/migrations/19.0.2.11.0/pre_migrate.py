import logging

_logger = logging.getLogger(__name__)

# Etapa 1 del rediseño de 3 ejes de estado (ver especificaciones/REQUISITOS.md):
# 2 columnas nuevas en ve_wh_iva, ambas campos compute+store que la propia
# actualización del módulo recalcula para todos los registros existentes.
# Este ALTER es solo la red de seguridad de siempre — evita el "column does
# not exist" si el upgrade del módulo no corriera completo (ver
# feedback_migraciones_odoo).
#   - estado_declaracion: espejo de state=='declarado' (Eje 3).
#   - estado_recepcion: state + diferencia de monto del comprobante (Eje 1).


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS estado_declaracion varchar DEFAULT 'no_declarado';
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS estado_recepcion varchar;
    """)
    _logger.info(
        've_retencion_iva 19.0.2.11.0: columnas estado_declaracion/'
        'estado_recepcion agregadas a ve_wh_iva (Etapa 1 del rediseño de 3 ejes)')
