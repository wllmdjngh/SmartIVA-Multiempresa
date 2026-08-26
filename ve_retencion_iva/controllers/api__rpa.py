import json
import logging
import re
from datetime import date as _date

from odoo import http, fields
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

_API_KEY_PARAM = 've_retencion_iva.rpa_api_key'
_REQUIRED_RET_FIELDS = {'nro_control', 'rif_agente', 'monto_retenido'}


def _calc_periodo_retencion(periodo, fecha_str, provided=''):
    """Calcula el período de retención quincenal a partir de la FECHA REAL
    de la retención — no del `periodo` que se pidió en la llamada.

    Regla: día <= 15 → 1Q del propio mes de la fecha; día >= 16 → 2Q del
    propio mes de la fecha — SIN desplazar al mes siguiente. La Fecha
    Documento del SENIAT es la fecha en que el agente de retención
    procesó/declaró el comprobante, y determina directamente la quincena
    en la que el crédito fiscal se puede aprovechar (no la ventana en la
    que el agente entera el impuesto ante el SENIAT, que sí puede caer
    días después). Misma regla que ya usa `ve.conciliacion.periodo.
    _asegurar_periodo()` para CONECTA-14 — antes de este fix, esta función
    quedaba inconsistente con esa (aquí calculaba 2Q/1Q invertidos y con
    salto de mes).

    Bug real encontrado 2026-08-01 (Cementos): la versión original (antes
    de cualquier fix) calculaba esto en base al `periodo` PEDIDO (ej.
    "2025-11"), y cuando la fecha de la retención no encajaba en las 2
    ventanas esperadas relativas a ESE período (ej. SENIAT devolvió
    también retenciones de diciembre al pedir noviembre), caía en
    silencio al default '{periodo} 2Q'. Calculando siempre desde la fecha
    real, cada retención cae en su período verdadero sin importar cuál
    se haya pedido — luego action_confirmar/_asegurar_periodo ya sabe
    crear ese período si hace falta."""
    if provided and provided.strip():
        return provided.strip()
    if fecha_str:
        try:
            fecha = _date.fromisoformat(str(fecha_str)[:10])
            quincena = '1Q' if fecha.day <= 15 else '2Q'
            return f'{fecha.year:04d}-{fecha.month:02d} {quincena}'
        except (ValueError, TypeError):
            pass
    # Sin fecha utilizable: único caso donde se recurre al período pedido.
    return f'{periodo} 2Q' if periodo else ''


def _check_api_key():
    # .strip() en ambos lados — mismo bug ya encontrado 2026-07-29 en las
    # API Keys de OCR (ve_comprobante_inbox.py/wizard_subir_comprobante.py):
    # un salto de línea al pegar la key en el Parámetro del Sistema (o al
    # copiarla desde ahí hacia AET) rompe la comparación exacta aunque el
    # valor "se vea" igual.
    key = (request.httprequest.headers.get('X-API-Key') or '').strip()
    stored = (request.env['ir.config_parameter'].sudo().get_param(_API_KEY_PARAM) or '').strip()
    return bool(stored and key == stored)


def _find_company(rif_empresa):
    """Resuelve la compañía (empresa) por su propio RIF — identificador
    requerido en cada llamada entrante del RPA (2026-07-24: sin esto, 2+
    clientes con período "aprobado" el mismo mes eran ambiguos —
    ejecutar_declaracion/registrar_resultado tomaban el primero que
    encontraran). No confundir con el "RIF_Cliente" de las llamadas
    SALIENTES (Odoo→RPA, ver ve_conciliacion.py::action_extraer_seniat) —
    ese identifica la BD de AET, este busca directo el res.company real
    por su propio RIF (2026-07-26). Las credenciales SENIAT NO se manejan
    acá — viven en el vault de AutomationEdge; Odoo nunca las recibe ni
    las guarda."""
    if not rif_empresa:
        return None
    # Comparación normalizada (sin guión/espacios, mayúsculas) — mismo
    # criterio ya usado en ve_comprobante_inbox.py/wizard_subir_comprobante.py.
    # Sin esto, un desfase de formato entre el `vat` real de la compañía
    # (ej. sin guión, porque así lo necesita AET) y lo que mande el RPA en
    # el payload deja la compañía "no encontrada" en silencio — bug real
    # encontrado 2026-07-28 en el wizard de reinicio del piloto (mismo
    # síntoma, otro punto del código: búsqueda exacta contra `vat`).
    rif_norm = re.sub(r'[-\s]', '', rif_empresa).upper()
    companies = request.env['res.company'].sudo().search([])
    for company in companies:
        if re.sub(r'[-\s]', '', company.vat or '').upper() == rif_norm:
            return company
    return None


def _resp(data, status=200):
    return Response(
        json.dumps(data),
        status=status,
        content_type='application/json',
    )


def _error(message, status=400):
    return _resp({'success': False, 'error': message}, status=status)


def _ok(**kwargs):
    return _resp({'success': True, **kwargs})


class RpaController(http.Controller):

    # ── 1. Cargar retenciones SENIAT ─────────────────────────────────────────

    @http.route(
        '/api/ve/seniat/cargar_retenciones',
        type='http', auth='none', methods=['POST'], csrf=False,
    )
    def cargar_retenciones(self):
        """
        El RPA llama a este endpoint para cargar el lote de retenciones
        descargadas del portal SENIAT para un período fiscal.

        Body JSON:
        {
            "rif_empresa": "J003168793",
            "periodo": "2024-01",
            "conciliacion_id": 5,
            "retenciones": [
                {
                    "nro_control":      "00-00000001",               (requerido)
                    "rif_agente":       "J-12345678-9",              (requerido)
                    "monto_retenido":   75.00,                       (requerido)
                    "nombre_agente":    "Empresa Ejemplo C.A.",
                    "fecha":            "2024-01-15",
                    "tipo_documento":   "Factura",
                    "nro_documento":    "00000001",
                    "nro_comprobante":  "00-12345678-00-000000001",
                    "doc_afectado":     "",
                    "alicuota":         16.0,
                    "monto_documento":  1000.00,
                    "monto_base":       1000.00,
                    "monto_exento":     0.00
                }
            ]
        }

        "rif_empresa" identifica la compañía cliente — requerido SOLO si no
        se manda "conciliacion_id" directo (ese ya desambigua por sí solo).
        Sin ninguno de los dos, 2+ clientes con el mismo período fiscal eran
        ambiguos (2026-07-24). Solo se necesita el RIF de la empresa acá —
        a diferencia de las llamadas SALIENTES (Odoo→RPA), Odoo no necesita
        saber en qué base de AET se ejecutó, solo encontrar su propio
        registro (2026-07-26).

        Respuesta:
        {
            "success": true,
            "periodo": "2024-01",
            "creadas": 10,
            "duplicadas": 2,
            "actualizadas": 1,
            "errores": []
        }
        """
        if not _check_api_key():
            return _error('API key inválida o ausente.', status=401)

        try:
            payload = json.loads(request.httprequest.data)
        except (ValueError, TypeError):
            return _error('Cuerpo de la solicitud no es JSON válido.')

        periodo = payload.get('periodo')
        retenciones_data = payload.get('retenciones', [])

        _logger.info(
            've_retencion_iva cargar_retenciones: periodo=%r  conciliacion_id=%r  '
            'n_retenciones=%d  primera_fecha=%r',
            periodo,
            payload.get('conciliacion_id'),
            len(retenciones_data) if isinstance(retenciones_data, list) else -1,
            retenciones_data[0].get('fecha') if isinstance(retenciones_data, list)
            and retenciones_data else None,
        )

        SeniatRet = request.env['ve.seniat.retencion'].sudo()
        ConcModel = request.env['ve.conciliacion.periodo'].sudo()

        # El RPA puede enviar conciliacion_id directamente (= Odoo_Conciliacion_ID).
        # Si lo hace: usamos esa conciliación para todos los registros y derivamos
        # el periodo_retencion correcto sin depender de la fecha de cada retención.
        conc_directa = None
        conc_pr_default = ''
        payload_conc_id = payload.get('conciliacion_id')
        if payload_conc_id:
            try:
                c = ConcModel.browse(int(payload_conc_id))
                if c.exists():
                    conc_directa = c
                    conc_pr_default = c.periodo_retencion or ''
                    if not periodo:
                        periodo = c.periodo
            except (ValueError, TypeError):
                pass

        if not periodo:
            return _error('El campo "periodo" es requerido (formato YYYY-MM).')
        if not isinstance(retenciones_data, list) or not retenciones_data:
            return _error('Se requiere un array "retenciones" no vacío.')

        # "rif_empresa" identifica al cliente — requerido solo si no se mandó
        # conciliacion_id directo (ese ya desambigua compañía por sí solo).
        # Sin esto, 2+ clientes con el mismo período fiscal eran ambiguos.
        company = None
        if not conc_directa:
            rif_empresa = payload.get('rif_empresa')
            if not rif_empresa:
                return _error(
                    'El campo "rif_empresa" es requerido cuando no se manda '
                    '"conciliacion_id" — identifica a qué cliente pertenece el período.')
            company = _find_company(rif_empresa)
            if not company:
                return _error(f'No existe ninguna compañía con RIF "{rif_empresa}".', status=404)

        # Fallback genérico por periodo fiscal cuando no hay conciliación directa
        domain_generica = [('periodo', '=', periodo)]
        if company:
            domain_generica.append(('company_id', '=', company.id))
        conc_generica = conc_directa or ConcModel.search(domain_generica, limit=1)
        # Compañía para el chequeo de duplicados (ver más abajo) -- la misma
        # que ya se usa para marcar es_agente_retencion al final.
        company_dedup = company or (conc_directa.company_id if conc_directa else False)
        # Cache de conciliaciones por periodo_retencion (evita queries repetidas)
        _conc_cache = {}
        creadas = 0
        duplicadas = 0
        actualizadas = 0
        fuera_quincena = 0
        errores = []
        # Períodos que esta llamada tocó de verdad — para actualizar
        # estado_extraccion al final (ver §2 del plan de "Retenciones SENIAT
        # por Período"). conc_directa aplica a todo el request si vino.
        conciliaciones_tocadas = conc_directa if conc_directa else ConcModel.browse()

        for i, item in enumerate(retenciones_data):
            missing = _REQUIRED_RET_FIELDS - set(item.keys())
            if missing:
                errores.append({
                    'index': i,
                    'nro_control': item.get('nro_control'),
                    'error': f'Campos faltantes: {", ".join(sorted(missing))}',
                })
                continue

            # GAP-SENIAT-CTRL-01: antes solo se validaba que la clave
            # 'nro_control' existiera en el JSON, no que tuviera contenido --
            # una fila con nro_control='' se creaba igual, a diferencia del
            # wizard manual (wizard_carga_seniat.py) que sí la descartaba.
            # Mismo criterio unificado en los 2 canales: N° Control vacío
            # por sí solo NO descarta (SENIAT a veces no lo trae y la
            # conciliación se hace por N° Documento) -- solo se descarta si
            # AMBOS identificadores faltan.
            if not item.get('nro_control') and not item.get('nro_documento'):
                errores.append({
                    'index': i,
                    'nro_control': item.get('nro_control'),
                    'error': 'N° Control y N° Documento vacíos — sin forma de identificar la fila',
                })
                continue

            # Calcular periodo_retencion natural desde la fecha de la retención
            pr = _calc_periodo_retencion(
                periodo, item.get('fecha'), item.get('periodo_retencion', ''))

            if conc_directa:
                fecha_str = item.get('fecha')
                if fecha_str:
                    # Filtrar retenciones que no pertenecen a la quincena de esta declaración
                    if pr != conc_directa.periodo_retencion:
                        fuera_quincena += 1
                        continue
                else:
                    # Sin fecha: asignar al período de la declaración
                    pr = conc_directa.periodo_retencion
                conciliacion = conc_directa
            else:
                # Sin conciliacion_id directa: buscar por periodo_retencion (+ compañía
                # del "rif_empresa", ya validado arriba) o CREAR el período si no existe
                # todavía. Bug real 2026-08-01 (Cementos): si la quincena calculada (pr)
                # no tenía período creado en Odoo, esto caía al "genérico" (cualquier
                # período existente de ese mes) — 3004 retenciones SENIAT quedaron 100%
                # vinculadas a "1Q" pese a que 2556 eran realmente de "2Q", porque el
                # período 2Q nunca se había creado. _asegurar_periodo() (mismo helper que
                # ya usa CONECTA-14 para esto mismo) crea el período que corresponde a la
                # fecha real de la retención en vez de conformarse con cualquiera.
                if pr and pr not in _conc_cache:
                    domain_pr = [('periodo_retencion', '=', pr), ('company_id', '=', company.id)]
                    encontrado = ConcModel.search(domain_pr, limit=1)
                    if not encontrado:
                        fecha_ref = item.get('fecha') or f'{periodo}-01'
                        encontrado = ConcModel._asegurar_periodo(company, fecha_ref)
                    _conc_cache[pr] = encontrado
                conciliacion = (pr and _conc_cache.get(pr)) or conc_generica

            if conciliacion:
                conciliaciones_tocadas |= conciliacion
                # Período "hermano" auto-creado a mitad de esta corrida por
                # _asegurar_periodo() (ej. la primera retención con fecha
                # ≥16 dispara la creación de la 2Q) nace en 'pendiente' —
                # nunca pasa por action_extraer_seniat() como el período
                # original. Bug real 2026-08-04/05 (Cementos, 2026-02 2Q,
                # id 913): el mensaje final de AET solo cierra hermanos con
                # estado_extraccion=='iniciada' (message_post(), ver
                # ve_conciliacion.py) — un hermano que se queda en
                # 'pendiente' nunca se encuentra ahí y queda huérfano para
                # siempre pese a recibir datos reales (588 retenciones
                # vinculadas, estado nunca avanzó). Se marca 'iniciada' en
                # cuanto este endpoint lo toca por primera vez.
                if conciliacion.estado_extraccion == 'pendiente':
                    conciliacion.estado_extraccion = 'iniciada'

            # Bug real encontrado 2026-08-05 (pedido explícito de la
            # usuaria): filtrar por `periodo` (el mes fiscal PEDIDO en esta
            # llamada) dejaba pasar como "nueva" una retención que SENIAT ya
            # había reportado antes bajo OTRO período -- la identidad real
            # de una retención es N° Control + N° Documento + RIF Agente +
            # compañía, el período es solo una clasificación derivada de su
            # fecha, no parte de su identidad. `company_id` se agrega porque
            # N° Control no es único entre compañías (cada una numera sus
            # propios comprobantes).
            #
            # Segundo bug real encontrado el mismo día, ANTES de llegar a
            # producción (Cementos, verificado por RPC): "N° Control" NO es
            # un identificador confiable por sí solo -- muchos agentes lo
            # reportan como "00" genérico (34 retenciones reales de
            # Cementos, 5 RIF distintos con 2 a 5 retenciones cada uno,
            # TODAS con nro_control="00" pero N° Documento real distinto en
            # cada una). Sin N° Documento en la clave, el fix de arriba
            # habría fusionado esas retenciones genuinamente distintas como
            # si fueran la misma -- se agrega N° Documento a la identidad.
            domain_dedup = [
                ('nro_control',   '=', item['nro_control']),
                ('nro_documento', '=', item.get('nro_documento', '')),
                ('rif_agente',    '=', item['rif_agente']),
            ]
            if company_dedup:
                domain_dedup.append(('company_id', '=', company_dedup.id))
            else:
                domain_dedup.append(('periodo', '=', periodo))  # respaldo si no se pudo resolver compañía
            ya_existe = SeniatRet.search(domain_dedup, limit=1)

            if ya_existe:
                if not ya_existe.conciliacion_id and conciliacion:
                    ya_existe.write({'conciliacion_id': conciliacion.id})
                # Se repite (mismo N° Control + N° Documento + RIF Agente +
                # compañía ya visto, sin importar el período) -- confirmado
                # 2026-08-04 (Cementos, enero): el propio SENIAT exporta la
                # misma retención más de una vez en algunos casos (5 de 1993
                # filas del archivo real). Se lleva la cuenta Y el detalle
                # (N° Control + N° Documento) en el período para poder
                # auditarlo después -- antes solo quedaba el conteo, sin
                # forma de saber cuáles habían sido (pedido explícito de la
                # usuaria 2026-08-05).
                periodo_dueno = ya_existe.conciliacion_id or conciliacion
                if periodo_dueno:
                    periodo_dueno.extraccion_repetidas += 1
                    detalle_previo = periodo_dueno.extraccion_repetidas_detalle or ''
                    linea_detalle = (
                        f"{item['nro_control']} / doc {item.get('nro_documento', '')} "
                        f"/ {item['rif_agente']}"
                    )
                    periodo_dueno.extraccion_repetidas_detalle = (
                        f'{detalle_previo}\n{linea_detalle}' if detalle_previo else linea_detalle
                    )
                    _logger.info(
                        've_retencion_iva cargar_retenciones: retención repetida '
                        'nro_control=%r nro_documento=%r rif_agente=%r periodo_dueno_id=%s',
                        item['nro_control'], item.get('nro_documento', ''),
                        item['rif_agente'], periodo_dueno.id,
                    )
                # Si el período de esta retención ya fue declarado, es
                # inmutable — se cuenta como duplicada (comportamiento de
                # siempre). Si NO está declarado, se actualiza con el dato
                # más reciente que trae el RPA en vez de ignorarlo.
                if periodo_dueno and periodo_dueno.estado == 'declarado':
                    duplicadas += 1
                else:
                    ya_existe.write({
                        'name':              item.get('nro_comprobante', ''),
                        'nombre_agente':     item.get('nombre_agente', ''),
                        'fecha':             item.get('fecha'),
                        'tipo_documento':    item.get('tipo_documento', ''),
                        'nro_documento':     item.get('nro_documento', ''),
                        'doc_afectado':      item.get('doc_afectado', ''),
                        'alicuota':          float(item.get('alicuota', 0.0)),
                        'monto_documento':   float(item.get('monto_documento', 0.0)),
                        'monto_base':        float(item.get('monto_base', 0.0)),
                        'monto_exento':      float(item.get('monto_exento', 0.0)),
                        'monto_retenido':    float(item['monto_retenido']),
                    })
                    actualizadas += 1
                continue

            try:
                _logger.info(
                    've_retencion_iva crear: nro_control=%r  periodo=%r  pr=%r  '
                    'fecha=%r  conc_id=%r',
                    item.get('nro_control'), periodo, pr,
                    item.get('fecha'),
                    conciliacion.id if conciliacion else False,
                )
                SeniatRet.create({
                    'name':              item.get('nro_comprobante', ''),
                    'rif_agente':        item['rif_agente'],
                    'nombre_agente':     item.get('nombre_agente', ''),
                    'periodo':           periodo,
                    'periodo_retencion': pr,
                    'fecha':             item.get('fecha'),
                    'tipo_documento':    item.get('tipo_documento', ''),
                    'nro_documento':     item.get('nro_documento', ''),
                    'nro_control':       item.get('nro_control', ''),
                    'doc_afectado':      item.get('doc_afectado', ''),
                    'alicuota':          float(item.get('alicuota', 0.0)),
                    'monto_documento':   float(item.get('monto_documento', 0.0)),
                    'monto_base':        float(item.get('monto_base', 0.0)),
                    'monto_exento':      float(item.get('monto_exento', 0.0)),
                    'monto_retenido':    float(item['monto_retenido']),
                    'cargado_por_rpa':   True,
                    'conciliacion_id':   conciliacion.id if conciliacion else False,
                })
                creadas += 1
            except Exception as exc:
                _logger.exception(
                    've_retencion_iva RPA: error creando retención SENIAT (index %d)', i)
                errores.append({
                    'index': i,
                    'nro_control': item.get('nro_control'),
                    'error': str(exc),
                })

        # Estado de extracción por período (ver plan "Retenciones SENIAT por
        # Período" — completa el checkpoint que faltaba: hoy action_extraer_
        # seniat() marca 'iniciada', pero nada marcaba 'completada'/'fallo'.
        #
        # Bug real encontrado 2026-08-04 (Cementos, período 2025-12 1Q): el
        # AET NO manda el lote completo en una sola llamada -- llama este
        # endpoint UNA VEZ POR CADA RETENCIÓN individual (confirmado en el
        # log de AET: 1770 POSTs separados, cada uno con "retenciones": [1
        # item]). El `conc.message_post(...)` que iba acá crasheaba la
        # transacción COMPLETA en cada una de esas 1770 llamadas —
        # traceback real (odoo.log): mail_thread.py línea "if not
        # author_id and self.env.user._is_public()" → `self.env.user`
        # está VACÍO bajo `auth='none'` (no hay usuario, ni siquiera el
        # público) → `ensure_one()` truena con "Expected singleton:
        # res.users()". Como la ruta es `type='http'`, la excepción no
        # manejada devuelve 500 y Odoo hace rollback de TODA la
        # transacción de esa llamada — el `SeniatRet.create()` de esa
        # misma retención se revertía junto con todo lo demás, por eso no
        # quedaba nada guardado pese al mensaje final "1770 registradas"
        # (ese mensaje lo postea AET aparte, con su propia sesión
        # autenticada -- eso SÍ funciona, y ya lo reformatea
        # message_post() de ve.conciliacion.periodo). Se quita por
        # completo el message_post de acá: es redundante (ese mismo
        # resultado ya lo cubre el mensaje final de AET).
        #
        # Segundo bug real encontrado el mismo día (Cementos, 2026-01): con
        # el mismo llamado-por-retención de arriba, marcar 'completada' en
        # CADA llamada exitosa dejaba el período en "Completada" mientras
        # la extracción seguía corriendo en el RPA (apenas la primera
        # retención se guardaba bien, ya se veía "Completada" en Odoo con
        # cientos/miles más todavía por llegar). El paso a 'completada'
        # queda 100% en manos del mensaje final real que postea AET al
        # terminar TODO el mes (interceptado por message_post() de
        # ve.conciliacion.periodo, que sí sabe cuándo terminó de verdad).
        # Acá solo se marca 'fallo' de inmediato si ESTA llamada puntual
        # tuvo errores -- un fallo real no debe esperar al mensaje final
        # para hacerse visible.
        if conciliaciones_tocadas and errores and not creadas:
            resumen = f'{duplicadas} duplicadas, {actualizadas} actualizadas, {len(errores)} error(es)'
            conciliaciones_tocadas.write({
                'estado_extraccion': 'fallo',
                'fecha_estado_extraccion': fields.Datetime.now(),
                'mensaje_estado_extraccion': resumen,
            })

        # Una retención SENIAT real para un RIF no marcado como Agente de
        # Retención es una contradicción entre el Libro de Ventas del
        # cliente y SENIAT que amerita revisión humana -- YA NO se marca
        # sola (pedido explícito 2026-08-18, ver res_partner.py::
        # _detectar_agentes_retencion_por_rif). Se reporta en la respuesta
        # para que el RPA/quien la reciba la deje visible para revisión.
        rifs_no_spe = []
        company_marca = company or (conc_directa.company_id if conc_directa else False)
        if company_marca:
            rifs_periodo = {item.get('rif_agente') for item in retenciones_data if item.get('rif_agente')}
            no_spe = request.env['res.partner'].sudo()._detectar_agentes_retencion_por_rif(
                company_marca, rifs_periodo)
            rifs_no_spe = [{'rif': p.vat, 'nombre': p.name} for p in no_spe]

        return _ok(
            periodo=periodo,
            creadas=creadas,
            duplicadas=duplicadas,
            actualizadas=actualizadas,
            fuera_quincena=fuera_quincena,
            errores=errores,
            rifs_retencion_sin_spe=rifs_no_spe,
        )

    # ── 2. Ejecutar declaración ───────────────────────────────────────────────

    @http.route(
        '/api/ve/declaracion/ejecutar',
        type='http', auth='none', methods=['POST'], csrf=False,
    )
    def ejecutar_declaracion(self):
        """
        El RPA llama a este endpoint para obtener los datos necesarios
        antes de ejecutar la declaración en el portal SENIAT.
        Valida que el período esté aprobado y devuelve los totales.

        Body JSON:
        {
            "rif_empresa": "J003168793",
            "periodo": "2024-01"
        }

        "rif_empresa" identifica la compañía cliente — requerido desde
        2026-07-24 (antes solo buscaba por período, ambiguo con 2+ clientes
        en el mismo mes). Las credenciales SENIAT NO viajan acá — quedan en
        el vault de AutomationEdge. Solo se necesita el RIF de la empresa
        (no el del cliente/BD de AET) — Odoo solo busca su propio registro.

        Respuesta:
        {
            "success": true,
            "periodo": "2024-01",
            "conciliacion_id": 5,
            "total_odoo": 1500.00,
            "total_seniat": 1500.00,
            "diferencia": 0.00,
            "conciliadas": 20,
            "con_diferencia": 0
        }
        """
        if not _check_api_key():
            return _error('API key inválida o ausente.', status=401)

        try:
            payload = json.loads(request.httprequest.data)
        except (ValueError, TypeError):
            return _error('Cuerpo de la solicitud no es JSON válido.')

        periodo = payload.get('periodo')
        rif_empresa = payload.get('rif_empresa')
        if not periodo:
            return _error('El campo "periodo" es requerido.')
        if not rif_empresa:
            return _error('El campo "rif_empresa" es requerido — identifica a qué cliente pertenece el período.')
        company = _find_company(rif_empresa)
        if not company:
            return _error(f'No existe ninguna compañía con RIF "{rif_empresa}".', status=404)

        conciliacion = request.env['ve.conciliacion.periodo'].sudo().search([
            ('periodo', '=', periodo),
            ('estado',  '=', 'aprobado'),
            ('company_id', '=', company.id),
        ], limit=1)

        if not conciliacion:
            return _error(
                f'No existe período de conciliación aprobado para "{periodo}" en {company.name}.',
                status=404,
            )

        return _ok(
            periodo=periodo,
            conciliacion_id=conciliacion.id,
            total_odoo=conciliacion.total_odoo,
            total_seniat=conciliacion.total_seniat,
            diferencia=conciliacion.diferencia,
            conciliadas=conciliacion.conciliadas,
            con_diferencia=conciliacion.con_diferencia,
        )

    # ── 3. Registrar resultado de declaración ─────────────────────────────────

    @http.route(
        '/api/ve/declaracion/registrar_resultado',
        type='http', auth='none', methods=['POST'], csrf=False,
    )
    def registrar_resultado(self):
        """
        El RPA llama a este endpoint una vez completada la declaración
        en el portal SENIAT, para registrar el número de declaración obtenido.

        Body JSON:
        {
            "rif_empresa":       "J-90000000-0",
            "periodo":           "2024-01",
            "nro_declaracion":   "240100000123456",
            "fecha_declaracion": "2024-02-15T10:30:00"
        }

        "rif_empresa" identifica la compañía cliente — requerido desde
        2026-07-24, mismo motivo que ejecutar_declaracion.

        Respuesta:
        {
            "success": true,
            "periodo": "2024-01",
            "nro_declaracion": "240100000123456",
            "estado": "declarado"
        }
        """
        if not _check_api_key():
            return _error('API key inválida o ausente.', status=401)

        try:
            payload = json.loads(request.httprequest.data)
        except (ValueError, TypeError):
            return _error('Cuerpo de la solicitud no es JSON válido.')

        periodo = payload.get('periodo')
        rif_empresa = payload.get('rif_empresa')
        nro_declaracion = payload.get('nro_declaracion')
        fecha_declaracion = payload.get('fecha_declaracion')

        if not all([periodo, rif_empresa, nro_declaracion, fecha_declaracion]):
            return _error(
                'Los campos "rif_empresa", "periodo", "nro_declaracion" y '
                '"fecha_declaracion" son requeridos.'
            )
        company = _find_company(rif_empresa)
        if not company:
            return _error(f'No existe ninguna compañía con RIF "{rif_empresa}".', status=404)

        conciliacion = request.env['ve.conciliacion.periodo'].sudo().search([
            ('periodo', '=', periodo),
            ('estado',  '=', 'aprobado'),
            ('company_id', '=', company.id),
        ], limit=1)

        if not conciliacion:
            return _error(
                f'No existe período de conciliación aprobado para "{periodo}" en {company.name}.',
                status=404,
            )

        try:
            conciliacion.action_registrar_declaracion(nro_declaracion, fecha_declaracion)
        except Exception as exc:
            _logger.exception(
                've_retencion_iva RPA: error registrando resultado de declaración para %s', periodo)
            return _error(str(exc), status=500)

        return _ok(
            periodo=periodo,
            nro_declaracion=nro_declaracion,
            estado='declarado',
        )
