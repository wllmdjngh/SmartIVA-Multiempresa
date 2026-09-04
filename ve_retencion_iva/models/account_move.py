import logging
import re

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

_NRO_CONTROL_RE = re.compile(r'^\d{2}-\d{7}$')


class AccountMove(models.Model):
    _inherit = 'account.move'

    nro_control = fields.Char(
        string='N° Control',
        help='Número de control de la factura. Formato: 00-0000000.\n'
             'La retención al 100% (PA SNAT/2025/000054) solo aplica si '
             'ADEMÁS falta N° Factura -- ver ese campo.',
        copy=False,
    )
    nro_factura = fields.Char(
        string='N° Factura',
        help='Número de Factura del cliente, tal cual viene en su Libro de '
             'Ventas. Junto con N° Control identifica la factura para '
             'efectos de retención -- el 100% (PA SNAT/2025/000054) solo '
             'aplica si faltan AMBOS (decisión confirmada con el cliente '
             '2026-08-21: un N° Factura válido basta para no penalizar).',
        copy=False,
    )
    zona = fields.Char(
        string='Zona/Planta',
        help='Identificador de zona/planta/sucursal de origen de la venta — '
             'informativo, un solo RIF/una sola Declaración IVA para todas '
             '(no es multi-compañía). Viene del feed de CONECTA-14 cuando el '
             'cliente factura desde varios puntos y quiere seguimiento '
             'separado de sus comprobantes por zona.',
        copy=False,
    )
    ve_wh_iva_ids = fields.One2many(
        've.wh.iva',
        'invoice_id',
        string='Retenciones IVA',
        readonly=True,
    )
    ve_wh_iva_prov_ids = fields.One2many(
        've.wh.iva.prov',
        'invoice_id',
        string='Retenciones IVA Proveedores',
        readonly=True,
    )
    ve_wh_iva_prov_count = fields.Integer(
        compute='_compute_ve_wh_iva_prov_count',
        string='Ret. IVA Prov.',
    )
    rif = fields.Char(string='RIF', related='partner_id.vat')
    es_agente_retencion = fields.Boolean(
        string='Contribuyente Especial', related='partner_id.es_agente_retencion')
    nota_credito_count = fields.Integer(
        string='Notas de Crédito',
        compute='_compute_nota_credito_count',
    )
    factura_afectada_nro_control = fields.Char(
        string='N° Control Fact. Afectada',
        related='reversed_entry_id.nro_control',
        readonly=True,
        help='N° Control de la FACTURA que esta Nota de Crédito afecta -- '
             'no confundir con "N° Control" arriba, que es el propio de '
             'esta NC (necesario para detectar duplicados/matching, no se '
             'toca).',
    )
    factura_afectada_nro_factura = fields.Char(
        string='N° Factura Afectada',
        related='reversed_entry_id.nro_factura',
        readonly=True,
    )
    factura_afectada_fecha = fields.Date(
        string='Fecha Fact. Afectada',
        related='reversed_entry_id.invoice_date',
        readonly=True,
    )

    @api.depends('ve_wh_iva_prov_ids')
    def _compute_ve_wh_iva_prov_count(self):
        for rec in self:
            rec.ve_wh_iva_prov_count = len(rec.ve_wh_iva_prov_ids)

    def action_view_wh_iva_prov(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Retenciones IVA Proveedores',
            'res_model': 've.wh.iva.prov',
            'view_mode': 'list,form',
            'domain': [('invoice_id', '=', self.id)],
            'context': {'default_invoice_id': self.id},
        }

    @api.depends('reversal_move_ids')
    def _compute_nota_credito_count(self):
        for rec in self:
            rec.nota_credito_count = len(rec.reversal_move_ids)

    def action_view_notas_credito(self):
        """Smart button de la factura original -- Odoo core (reversed_entry_id/
        reversal_move_ids) no expone este vínculo en ningún lado de la UI para
        facturas/NC (solo lo muestra para move_type=='entry'), a diferencia de
        Notas de Débito (account_debit_note SÍ trae su propio smart button +
        campo). Agregado 2026-09-04 a pedido de la usuaria, mismo patrón que
        action_view_debit_notes/debit_note_count del módulo nativo."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Notas de Crédito',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('reversed_entry_id', '=', self.id)],
        }

    def action_view_factura_afectada(self):
        """Smart button de la propia NC, apunta a reversed_entry_id (siempre
        una sola factura -- Many2one, no Many2many). Agregado 2026-09-04:
        el campo equivalente (visible junto a Origen, ver
        account_move_views.xml) queda enterrado en la pestaña "Otra
        Información" -- mismo lugar donde Odoo esconde debit_origin_id
        para Notas de Débito -- así que se agrega también acá arriba para
        que sea tan visible como el smart button del lado factura."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.reversed_entry_id.id,
        }

    @api.onchange('nro_control')
    def _onchange_nro_control(self):
        if self.nro_control and not _NRO_CONTROL_RE.match(self.nro_control):
            return {
                'warning': {
                    'title': 'N° Control',
                    'message': (
                        'El N° Control debe tener el formato 00-0000000 '
                        '(2 dígitos, guion, 7 dígitos).'
                    ),
                }
            }

    def action_post(self):
        result = super().action_post()
        for move in self:
            if (move.move_type == 'out_invoice'
                    and move.partner_id.es_agente_retencion):
                move._ve_crear_retencion_esperada()
        return result

    def action_crear_retencion_esperada_manual(self):
        """Wrapper público de _ve_crear_retencion_esperada -- Odoo bloquea
        llamar métodos privados (con '_') por RPC directo ("Private methods
        cannot be called remotely"). Pedido explícito 2026-08-11 para el
        backfill de las 720 facturas de Cementos que quedaron sin retención
        por el bug de la regla "no retención entre SPE" ya eliminada (ver
        [[project_bug_no_retencion_entre_spe]]) -- no duplica lógica,
        solo abre la puerta para re-ejecutar la misma creación sobre
        facturas ya posteadas.

        Bug real encontrado 2026-08-12, DESPUÉS de correr el backfill de
        las 720 con la versión original de este método: a diferencia de
        CONECTA-14 (ve_conecta_carga_ventas.py::action_confirmar), este
        método no pasaba el contexto `ve_periodo_asignacion_manual` ni
        vinculaba el resultado por la fecha real de la factura -- el
        create() de ve.wh.iva enganchó las 720 retenciones nuevas al
        "período abierto más reciente" de la compañía (julio, porque la
        extracción SENIAT ya había creado períodos hasta ahí), en vez de
        Ene/Feb que es cuando ocurrieron las facturas reales. Corregido
        con el mismo patrón que ya usa CONECTA-14: contexto +
        _asegurar_periodo por la fecha real de cada factura."""
        Periodo = self.env['ve.conciliacion.periodo'].sudo()
        for move in self.with_context(ve_periodo_asignacion_manual=True):
            if move.move_type == 'out_invoice' and move.partner_id.es_agente_retencion:
                wh = move._ve_crear_retencion_esperada()
                if wh and not wh.conciliacion_id:
                    fecha_factura = move.invoice_date or fields.Date.today()
                    wh.conciliacion_id = Periodo._asegurar_periodo(
                        move.company_id, fecha_factura).id

    def action_anular_factura_neta_cero(self):
        """Anula (elimina) esta(s) factura(s) y su(s) retención(es) IVA --
        mismo procedimiento de reversión ya usado en
        ve_conecta_carga_ventas.py::action_deshacer (pagos → asiento de la
        retención → la factura misma), pero aplicable a CUALQUIER conjunto
        de facturas por id, no solo las de una carga completa.

        Construido 2026-08-12 para corregir el caso real "Registro +
        Anulación" de zona Invecem: bajo la lógica vieja de duplicados
        (antes de es_anulacion_par en ve_conecta_carga_ventas.py), el
        "Registro" de un par que neta a cero se colaba como factura real
        mientras su "Anulación" se descartaba como duplicada -- dejando
        65 facturas fantasma en Cementos (Bs 200,8 MM en Base Imponible,
        56 con retención ya creada) que no representan ninguna operación
        real. Autorizado explícitamente por la usuaria 2026-08-12."""
        Payment = self.env['account.payment'].sudo()
        WhIva = self.env['ve.wh.iva'].sudo()
        resultado = {'facturas': 0, 'retenciones': 0, 'pagos': 0, 'errores': []}
        for inv in self.sudo():
            try:
                pagos = inv._get_reconciled_payments()
            except AttributeError:
                pagos = Payment.browse()
            for pago in pagos:
                try:
                    with self.env.cr.savepoint():
                        if pago.state == 'paid' and hasattr(pago, 'action_draft'):
                            pago.action_draft()
                        pago.unlink()
                    resultado['pagos'] += 1
                except Exception as exc:
                    resultado['errores'].append(f'Pago de {inv.name or inv.id}: {exc}')

            for wh in WhIva.search([('invoice_id', '=', inv.id)]):
                try:
                    with self.env.cr.savepoint():
                        if wh.asiento_id and wh.asiento_id.state == 'posted':
                            wh.asiento_id.button_cancel()
                        wh.unlink()
                    resultado['retenciones'] += 1
                except Exception as exc:
                    resultado['errores'].append(f'Retención de {inv.name or inv.id}: {exc}')

            try:
                with self.env.cr.savepoint():
                    if inv.state == 'posted':
                        inv.button_draft()
                    inv.unlink()
                resultado['facturas'] += 1
            except Exception as exc:
                resultado['errores'].append(f'Factura {inv.name or inv.id}: {exc}')
        return resultado

    def _ve_crear_retencion_esperada(self):
        """Crea ve.wh.iva en estado 'esperado' al confirmar una factura SPE.

        ELIMINADA 2026-08-11 la regla "no retención entre SPE" (si la
        propia empresa también es SPE, se omitía la creación) -- confirmado
        explícitamente por la usuaria/contador que esa regla es incorrecta:
        no existe tal exención en la normativa venezolana de retención de
        IVA. Causó un bug real en Cementos: una fila del Libro de Ventas
        donde la propia empresa aparecía como su propio cliente (dato
        erróneo, Contribuyente='S') marcó a Cementos como agente de
        retención de sí misma, y esa regla bloqueó silenciosamente la
        creación de retenciones en TODAS las facturas posteriores durante
        más de un día -- ver [[project_bug_no_retencion_entre_spe]].
        """
        self.ensure_one()

        # Evitar duplicados (excluir anulados para permitir re-creación)
        existing = self.env['ve.wh.iva'].search(
            [('invoice_id', '=', self.id), ('state', '!=', 'anulado')], limit=1
        )
        if existing:
            return existing

        # Si no hay N° Control NI N° Factura → retención al 100%
        if not self.nro_control and not self.nro_factura:
            porcentaje = 100.0
            _logger.info(
                've_retencion_iva: factura %s sin N° Control ni N° Factura → '
                '100%% de retención',
                self.name,
            )
        else:
            porcentaje = (
                self.partner_id.porcentaje_retencion_default or 75.0
            )

        invoice_date = self.invoice_date or fields.Date.today()
        periodo = invoice_date.strftime('%Y-%m')

        # Montos por alícuota — una línea SIN ninguna alícuota 16%/8% es
        # venta exenta/no gravada (ej. cliente 100% exento) y NUNCA debe
        # sumarse a monto_base (Base Imponible 16%): antes el fallback de
        # "usar totales de la factura" metía el monto exento entero ahí,
        # etiquetándolo como si fuera base gravada al 16% — bug real
        # reportado en vivo (CLIENTE PILOTO SIETE CA, 100% exento).
        monto_base_16, monto_iva_16 = 0.0, 0.0
        monto_base_8, monto_iva_8 = 0.0, 0.0
        monto_exento = 0.0
        for line in self.invoice_line_ids:
            if line.display_type in ('line_section', 'line_note'):
                continue
            tasas = {round(t.amount, 2) for t in line.tax_ids if hasattr(t, 'amount')}
            if any(abs(t - 16.0) < 0.01 for t in tasas):
                monto_base_16 += line.price_subtotal
                monto_iva_16 += line.price_subtotal * 0.16
            elif any(abs(t - 8.0) < 0.01 for t in tasas):
                monto_base_8 += line.price_subtotal
                monto_iva_8 += line.price_subtotal * 0.08
            else:
                monto_exento += line.price_subtotal

        vals = {
            'state': 'esperado',
            'incluir_declaracion': False,   # Se activa automáticamente al recibir el comprobante
            'invoice_id': self.id,
            'partner_id': self.partner_id.id,
            'nro_control': self.nro_control or False,
            'nro_factura': self.nro_factura or False,
            'zona': self.zona or False,
            'periodo': periodo,
            'company_id': self.company_id.id,
            'monto_base': round(monto_base_16, 2),
            'monto_iva': round(monto_iva_16, 2),
            'monto_base_red': round(monto_base_8, 2),
            'monto_iva_red': round(monto_iva_8, 2),
            'monto_exento': round(monto_exento, 2),
            'porcentaje_retencion': porcentaje,
            # fecha se asigna solo al recibir el comprobante (action_recibir)
        }
        wh = self.env['ve.wh.iva'].create(vals)
        _logger.info(
            've_retencion_iva: creado ve.wh.iva id=%d para factura %s '
            '(partner=%s, porcentaje=%.0f%%)',
            wh.id, self.name, self.partner_id.name, porcentaje,
        )
        return wh
