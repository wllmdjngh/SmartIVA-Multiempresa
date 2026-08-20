import logging

_logger = logging.getLogger(__name__)

# Etapa 4 (punto sin retorno) del rediseño de 3 ejes de estado — ver
# especificaciones/REQUISITOS.md y el plan de ejecución. A partir de esta
# versión, 'conciliado'/'declarado' dejan de existir en la Selection de
# ve_wh_iva.state (Conciliación y Declaración viven en estado_conciliacion/
# estado_declaracion desde la Etapa 3). Este script migra por SQL crudo,
# ANTES de que el módulo cargue la nueva Selection restringida, cualquier
# fila que todavía tenga esos 2 valores legado.
#
# Reglas (mismo criterio que ya usaba action_deshacer_declaracion antes de
# la Etapa 3, y que _compute_estado_recepcion ya replica para mostrar estos
# casos correctamente incluso con el `state` legado todavía sin migrar):
#   1) state='declarado' + declarado_sin_comprobante=True + sin comprobante
#      físico (comp_monto_retenido nulo o 0) -> nunca llegó el papel; state
#      se recalcula a 'vencido' si ya pasó la fecha límite de entrega, o
#      'esperado' si no. estado_declaracion se deja en 'declarado' (si el
#      Eje 3 no lo tenía ya así, es una inconsistencia previa que se
#      corrige de paso).
#   2) state='declarado' (con comprobante) o state='conciliado' -> ya se
#      recibió/confirmó el papel en su momento; state pasa a 'confirmado'.


def migrate(cr, version):
    cr.execute("""
        SELECT count(*) FROM ve_wh_iva WHERE state IN ('conciliado', 'declarado')
    """)
    total_antes = cr.fetchone()[0]

    # Caso 1: declarado sin comprobante físico -> recalcular esperado/vencido
    cr.execute("""
        UPDATE ve_wh_iva
        SET state = CASE
                        WHEN fecha_vencimiento_entrega IS NOT NULL
                             AND fecha_vencimiento_entrega < CURRENT_DATE
                        THEN 'vencido'
                        ELSE 'esperado'
                     END,
            estado_declaracion = 'declarado'
        WHERE state = 'declarado'
          AND declarado_sin_comprobante = true
          AND (comp_monto_retenido IS NULL OR comp_monto_retenido = 0);
    """)
    caso1 = cr.rowcount

    # Caso 2: declarado con comprobante, o conciliado -> confirmado
    cr.execute("""
        UPDATE ve_wh_iva
        SET state = 'confirmado',
            estado_declaracion = 'declarado'
        WHERE state = 'declarado';
    """)
    caso2a = cr.rowcount

    cr.execute("""
        UPDATE ve_wh_iva
        SET state = 'confirmado'
        WHERE state = 'conciliado';
    """)
    caso2b = cr.rowcount

    _logger.info(
        've_retencion_iva 19.0.2.11.9 (Etapa 4): %d fila(s) tenían '
        "state en ('conciliado','declarado') -> %d recalculadas a "
        'esperado/vencido (sin comprobante), %d declarado->confirmado, '
        '%d conciliado->confirmado',
        total_antes, caso1, caso2a, caso2b,
    )
