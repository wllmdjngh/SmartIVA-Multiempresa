import logging

_logger = logging.getLogger(__name__)

# A3+A7 Paso 1 (2026-09-10): campo nuevo y separado para capturar "Tipo de
# Contribuyente" (Cerro Azul) / "Tipo de Clientes" (las otras 5 zonas) tal
# cual vienen en el Libro de Ventas -- NUNCA alimenta es_spe (esa sigue
# siendo la columna "Contribuyente" S/N/E del RPA). Ver
# models/ve_conecta_carga_ventas.py (_HEADER_MAP + campo
# tipo_cliente_archivo). Preguntas sobre confiabilidad/relación con SPE
# quedan diferidas a la sesión de análisis del proyecto.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_conecta_carga_ventas_linea
        ADD COLUMN IF NOT EXISTS tipo_cliente_archivo varchar
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.212: columna tipo_cliente_archivo '
        'asegurada en ve_conecta_carga_ventas_linea')
