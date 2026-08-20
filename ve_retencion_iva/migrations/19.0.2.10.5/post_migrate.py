import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # estado_visual (store=True) ahora distingue 4 sub-estados de "Declarado"
    # (declarado_seniat_ok/declarado_sin_seniat/declarado_sin_comp/
    # declarado_con_dif) en vez de reutilizar los badges de "Conciliado" —
    # los registros existentes no recalculan solos porque sus campos
    # dependientes (state, estado_conciliacion, etc.) no cambiaron de valor.
    env = api.Environment(cr, SUPERUSER_ID, {})
    records = env['ve.wh.iva'].search([('state', '=', 'declarado')])
    records._compute_estado_visual()
    env.flush_all()
    _logger.info(
        've_retencion_iva 19.0.2.10.5: estado_visual recalculado para %d '
        'retención(es) ya declaradas',
        len(records),
    )
