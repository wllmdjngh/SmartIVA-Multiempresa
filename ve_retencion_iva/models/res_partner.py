from odoo import api, models, fields


def _norm_rif(rif):
    return (rif or '').upper().replace('-', '').replace(' ', '').strip()


class ResPartner(models.Model):
    _inherit = 'res.partner'

    es_agente_retencion = fields.Boolean(
        string='Es Agente de Retención (SPE)',
        default=False,
        help='Marque si este cliente es Sujeto Pasivo Especial (SPE) '
             'y aplica retención IVA a nuestras facturas.',
    )
    porcentaje_retencion_default = fields.Float(
        string='% Retención por Defecto',
        digits=(5, 2),
        default=75.0,
        help='Porcentaje de retención estándar para este agente. '
             'Uso: 75% normal, 100% cuando aplique.',
    )
    validado_seniat = fields.Selection(
        [('si', 'Sí'), ('no', 'No')],
        string='Validado SENIAT',
        copy=False,
        help='En blanco al crear el Cliente. Se sincroniza con la columna '
             'opcional Validado_SENIAT de cada carga del Libro de Ventas '
             '(SmartIVA Conecta) -- Sí y No prevalecen sobre blanco, y Sí '
             'prevalece sobre No (ver ve_conecta_carga_ventas.py::'
             'action_confirmar).',
    )

    @api.model
    def _detectar_agentes_retencion_por_rif(self, company, rifs):
        """Detecta partners existentes cuyo RIF (normalizado) aparece en
        `rifs` (RIFs con retención SENIAT real en el período cargado) pero
        todavía NO están marcados como Agente de Retención -- ya NO los
        marca automáticamente (ver historial: hasta 2026-08-18 sí escribía
        es_agente_retencion=True acá, sin revisión).

        Pedido explícito de la usuaria 2026-08-18: encontramos casos reales
        en Vencement (ej. FERROELECTRICO MENDOZA, V-12230110-5) donde el
        Libro de Ventas del cliente trae Contribuyente Especial='E' (no
        agente) en TODAS sus filas, pero SENIAT sí reporta retención real
        para ese RIF -- una contradicción entre la fuente del cliente y la
        fuente regulatoria que amerita revisión humana, no una marca
        automática y silenciosa. Marcar sin revisar dejaba huérfanas (sin
        retención) todas las facturas de ese cliente posteadas ANTES de la
        carga SENIAT que disparaba la marca, porque el cambio no era
        retroactivo -- ver [[project_vencement_carga_datos_en_progreso]].

        Ahora el llamador (carga XLSX de SENIAT y endpoint RPA) es quien
        decide qué hacer con el resultado: típicamente reportarlo (chatter/
        respuesta del endpoint) para que alguien revise y marque a mano
        con conocimiento de causa, en vez de escribir el campo acá.

        No incluye partners sin contacto en Odoo (RIF no encontrado) --
        eso se resuelve al facturar, no al cargar SENIAT."""
        rifs_norm = {_norm_rif(r) for r in rifs if r}
        if not rifs_norm or not company:
            return self.browse()
        candidatos = self.search([
            ('vat', '!=', False),
            ('es_agente_retencion', '=', False),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ])
        return candidatos.filtered(lambda p: _norm_rif(p.vat) in rifs_norm)
