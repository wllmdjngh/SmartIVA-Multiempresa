import logging

_logger = logging.getLogger(__name__)

# categoria_discrepancia es campo nuevo (compute+store) -- el propio Odoo
# ya lo recalculó para todas las lineas existentes al actualizar el modulo
# (mismo mecanismo que cualquier campo compute+store nuevo), ANTES de que
# este script corra. Pero _compute_partner_id solo puede clasificar
# 'error_posteo' con certeza si la linea ya lo tenia marcado de un intento
# de action_confirmar anterior (ver el nuevo write() en la excepcion) -- las
# lineas que fallaron al postear ANTES de este fix (2026-08-14) nunca
# tuvieron ese write, asi que la primera pasada del compute las deja en
# categoria_discrepancia=NULL en vez de 'error_posteo' (caso real: 19 filas
# "Anulacion" huerfanas encontradas en Cementos, monto negativo que Odoo
# rechazo al postear). Este backfill las identifica por descarte: sin
# factura, sin categoria, en una carga ya terminal -- ya no pueden ser
# ninguna otra categoria (esas si las agarra bien el compute solo).


def migrate(cr, version):
    cr.execute("""
        UPDATE ve_conecta_carga_ventas_linea l
        SET categoria_discrepancia = 'error_posteo',
            brecha = 'Error al postear (motivo original no capturado -- '
                     'anterior al fix de categoria_discrepancia, 2026-08-14)'
        FROM ve_conecta_carga_ventas c
        WHERE l.carga_id = c.id
          AND l.invoice_id IS NULL
          AND l.categoria_discrepancia IS NULL
          AND c.estado IN ('confirmado', 'confirmado_discrepancias')
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.82: %d linea(s) sin factura y sin '
        'categoria reclasificadas como error_posteo (backfill)',
        cr.rowcount,
    )
