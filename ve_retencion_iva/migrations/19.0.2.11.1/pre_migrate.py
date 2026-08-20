import logging

_logger = logging.getLogger(__name__)

# Etapa 2 del rediseño de 3 ejes de estado (ver especificaciones/REQUISITOS.md):
#   - 3 columnas nuevas en ve_wh_iva (compute+store, la actualización del
#     módulo las recalcula solas): necesita_envio_comp,
#     necesita_aclarar_dif_seniat, necesita_reportar_seniat — reemplazan las
#     ~30 combinaciones de estado_visual para decidir qué botón de
#     recordatorio aplica.
#   - estado_visual (el badge combinado) se retira — sus ~30 valores ahora
#     se muestran como 3 badges separados (estado_recepcion,
#     estado_conciliacion, estado_declaracion, esta última de la Etapa 1).
#     Se elimina la columna en vez de dejarla huérfana.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS necesita_envio_comp boolean;
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS necesita_aclarar_dif_seniat boolean;
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS necesita_reportar_seniat boolean;
        ALTER TABLE ve_wh_iva
        DROP COLUMN IF EXISTS estado_visual;
    """)
    _logger.info(
        've_retencion_iva 19.0.2.11.1: columnas necesita_* agregadas y '
        'estado_visual eliminada de ve_wh_iva (Etapa 2 del rediseño de 3 ejes)')
