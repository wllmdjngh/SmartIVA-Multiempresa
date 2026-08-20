import logging

_logger = logging.getLogger(__name__)

# Rename de columna: "campo_33_arrastre" -> "campo_54_arrastre" en
# ve_declaracion_iva. El nombre viejo era un residuo histórico sin relación
# real con C.33 (que es Base de Compras, campo_33_base) — el campo real es
# C.54 "Retenciones Acumuladas por Descontar" (pedido explícito de la
# usuaria: los nombres técnicos deben reflejar la Forma 030 real, sin
# prestarse a confusión). RENAME COLUMN preserva los datos existentes.


def migrate(cr, version):
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 've_declaracion_iva' AND column_name = 'campo_33_arrastre'
    """)
    if cr.fetchone():
        cr.execute("""
            ALTER TABLE ve_declaracion_iva
            RENAME COLUMN campo_33_arrastre TO campo_54_arrastre;
        """)
        _logger.info(
            've_retencion_iva 19.0.2.11.6: columna campo_33_arrastre '
            'renombrada a campo_54_arrastre en ve_declaracion_iva (sin '
            'pérdida de datos)')
