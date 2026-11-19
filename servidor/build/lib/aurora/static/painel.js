"use strict";
// Painel da Aurora — sem bibliotecas externas: funciona mesmo sem internet na escola.
// Todos os textos vindos do servidor entram com textContent (nunca innerHTML).

(() => {
  const METRICAS = ["co2_ppm", "temperatura_c", "umidade_pct"];
  const ROTULO_CURTO = { co2_ppm: "CO₂ (ppm)", temperatura_c: "Temp. (°C)", umidade_pct: "Umidade (%)" };
  const CASAS = { co2_ppm: 0, temperatura_c: 1, umidade_pct: 0 };
  const FOLGA_MINIMA = { co2_ppm: 100, temperatura_c: 2, umidade_pct: 10 };
  const ATUALIZAR_A_CADA_MS = 60_000;
  const TEMPO_LIMITE_MS = 15_000;

  const estado = {
    token: lerArmazenado(sessionStorage, "aurora-token"),
    selecionado: lerArmazenado(localStorage, "aurora-sala"),
    horas: 24,
    refs: null,
    ultimoDetalhe: null,
    salas: [],
  };

  // ------------------------------------------------------------ utilidades

  function lerArmazenado(area, chave) {
    try { return area.getItem(chave); } catch { return null; }
  }
  function gravarArmazenado(area, chave, valor) {
    try { valor == null ? area.removeItem(chave) : area.setItem(chave, valor); } catch { /* modo privado */ }
  }
  const $ = (id) => document.getElementById(id);

  function el(tag, attrs = {}, ...filhos) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "class") e.className = v;
      else e.setAttribute(k, v);
    }
    for (const f of filhos) if (f != null) e.append(f instanceof Node ? f : String(f));
    return e;
  }

  function formatar(valor, metrica) {
    if (valor == null || !Number.isFinite(valor)) return "—";
    const casas = CASAS[metrica] ?? 1;
    return valor.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  }

  function tempoRelativo(iso) {
    if (!iso) return "nunca";
    const s = Math.round((Date.now() - Date.parse(iso)) / 1000);
    if (s < 60) return "agora há pouco";
    if (s < 3600) return `há ${Math.round(s / 60)} min`;
    if (s < 86400) return `há ${Math.round(s / 3600)} h`;
    return `há ${Math.round(s / 86400)} dia(s)`;
  }

  function cor(nome) {
    return getComputedStyle(document.documentElement).getPropertyValue(`--${nome}`).trim() || "#888";
  }

  function nivel(metrica, valor) {
    const r = estado.refs?.metricas?.[metrica];
    if (!r || valor == null) return "ok";
    if ((r.critico_acima != null && valor > r.critico_acima) || (r.critico_abaixo != null && valor < r.critico_abaixo)) return "critico";
    if ((r.atencao_acima != null && valor > r.atencao_acima) || (r.atencao_abaixo != null && valor < r.atencao_abaixo)) return "atencao";
    return "ok";
  }

  // ------------------------------------------------------------ comunicação

  class ErroHttp extends Error {
    constructor(status, mensagem) { super(mensagem); this.status = status; }
  }

  async function api(caminho, { comoArquivo = false } = {}) {
    const headers = {};
    if (estado.token) headers.Authorization = `Bearer ${estado.token}`;
    const controle = new AbortController();
    const relogio = setTimeout(() => controle.abort(), TEMPO_LIMITE_MS);
    let resposta;
    try {
      resposta = await fetch(caminho, { headers, signal: controle.signal, cache: "no-store" });
    } catch (e) {
      throw new ErroHttp(0, e.name === "AbortError"
        ? "O servidor demorou demais para responder."
        : "Sem conexão com o servidor da Aurora. Ele está ligado?");
    } finally {
      clearTimeout(relogio);
    }
    if (resposta.status === 401) {
      pedirToken();
      throw new ErroHttp(401, "É preciso informar o token do painel.");
    }
    if (!resposta.ok) {
      let msg = `Erro ${resposta.status}`;
      try {
        const corpo = await resposta.json();
        if (corpo?.detail) msg += `: ${typeof corpo.detail === "string" ? corpo.detail : JSON.stringify(corpo.detail)}`;
      } catch { /* corpo não era JSON */ }
      throw new ErroHttp(resposta.status, msg);
    }
    return comoArquivo ? resposta.blob() : resposta.json();
  }

  function mostrarErro(msg) { const e = $("erro"); e.textContent = msg; e.hidden = false; }
  function limparErro() { $("erro").hidden = true; }

  function pedirToken() {
    estado.token = null;
    gravarArmazenado(sessionStorage, "aurora-token", null);
    $("form-token").hidden = false;
    $("campo-token").focus();
  }

  // ------------------------------------------------------------ salas

  function desenharSalas(lista) {
    const grade = $("salas");
    grade.replaceChildren();
    $("sem-salas").hidden = lista.length > 0;

    for (const s of lista) {
      const piores = s.avisos.map((a) => a.nivel);
      const nivelSala = !s.online ? "offline" : piores.includes("critico") ? "critico" : piores.includes("atencao") ? "atencao" : "ok";
      const ultima = s.ultima_leitura;

      const valores = el("div", { class: "valores" });
      for (const m of METRICAS) {
        const v = ultima?.[m];
        valores.append(el("div", { class: "valor", "data-nivel": nivel(m, v) },
          el("span", { class: "numero" }, formatar(v, m)),
          el("span", { class: "rotulo" }, ROTULO_CURTO[m])));
      }

      const avisos = el("ul", { class: "avisos" });
      for (const a of s.avisos) avisos.append(el("li", {}, a.mensagem));

      const cartao = el("button", {
        type: "button", class: "cartao sala", "data-nivel": nivelSala,
        "aria-pressed": String(s.id === estado.selecionado),
      },
        el("div", { class: "sala-topo" },
          el("span", { class: "sala-nome" }, s.sala),
          el("span", { class: `selo ${s.online ? "online" : "offline"}` }, s.online ? "online" : "sem sinal")),
        valores,
        s.avisos.length ? avisos : null,
        el("span", { class: "quando" }, ultima ? `Medido ${tempoRelativo(ultima.medido_em)}` : "Ainda sem medições"));
      cartao.addEventListener("click", () => selecionar(s.id));
      grade.append(cartao);
    }
  }

  function selecionar(id) {
    estado.selecionado = id;
    gravarArmazenado(localStorage, "aurora-sala", id);
    desenharSalas(estado.salas);
    carregarDetalhe().catch(tratarErro);
  }

  // ------------------------------------------------------------ detalhe

  async function carregarDetalhe() {
    const id = estado.selecionado;
    const sala = estado.salas.find((s) => s.id === id);
    if (!sala) { $("detalhe").hidden = true; return; }
    const base = `/api/v1/dispositivos/${encodeURIComponent(id)}`;
    const intervalo = estado.horas <= 6 ? 5 : estado.horas <= 24 ? 10 : estado.horas <= 168 ? 60 : 180;
    const [serie, resumo, padroes] = await Promise.all([
      api(`${base}/serie?horas=${estado.horas}&intervalo_min=${intervalo}`),
      api(`${base}/resumo?horas=${estado.horas}`),
      api(`${base}/padroes?dias=7`),
    ]);
    if (estado.selecionado !== id) return; // o usuário trocou de sala no meio
    estado.ultimoDetalhe = { serie, resumo, padroes };
    $("titulo-detalhe").textContent = `Histórico — ${sala.sala}`;
    $("detalhe").hidden = false;
    desenharDetalhe();
  }

  function desenharDetalhe() {
    const d = estado.ultimoDetalhe;
    if (!d) return;
    desenharResumo(d.resumo);
    for (const m of METRICAS) desenharSerie($(`g-${m}`), d.serie, m);
    desenharPadrao($("g-padrao"), d.padroes.horas);
  }

  function desenharResumo(resumo) {
    const caixa = $("resumo");
    caixa.replaceChildren();
    for (const m of METRICAS) {
      const r = resumo.metricas[m];
      const nome = estado.refs?.metricas?.[m]?.nome ?? m;
      const dl = el("dl", {});
      const linha = (rotulo, valor) => dl.append(el("dt", {}, rotulo), el("dd", {}, valor));
      if (r) {
        linha("Média", formatar(r.media, m));
        linha("Mínimo", formatar(r.min, m));
        linha("Máximo", formatar(r.max, m));
        linha("Fora da referência", `${r.fora_da_referencia_pct.toLocaleString("pt-BR")}% do tempo`);
        linha("Leituras", r.leituras.toLocaleString("pt-BR"));
      } else {
        linha("Sem dados", "no período");
      }
      caixa.append(el("div", { class: "cartao resumo-item" }, el("h3", {}, nome), dl));
    }
  }

  // ------------------------------------------------------------ gráficos (canvas)

  function prepararCanvas(canvas) {
    canvas.dataset.altura ??= canvas.getAttribute("height");
    const altura = Number(canvas.dataset.altura);
    const largura = Math.max(canvas.clientWidth, 200);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(largura * dpr);
    canvas.height = Math.round(altura * dpr);
    canvas.style.height = `${altura}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = "12px system-ui, sans-serif";
    return { ctx, w: largura, h: altura };
  }

  function mensagemVazia(ctx, w, h, texto) {
    ctx.fillStyle = cor("suave");
    ctx.textAlign = "center";
    ctx.fillText(texto, w / 2, h / 2);
  }

  function rotuloTempo(ms, horas) {
    const d = new Date(ms);
    return horas <= 48
      ? d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }

  function passoBonito(bruto) {
    const potencia = 10 ** Math.floor(Math.log10(bruto));
    const fracao = bruto / potencia;
    return (fracao <= 1 ? 1 : fracao <= 2 ? 2 : fracao <= 2.5 ? 2.5 : fracao <= 5 ? 5 : 10) * potencia;
  }

  // Marcas do eixo Y em números redondos (ex.: 400, 600, 800 em vez de 413, 627...)
  function marcasY(min, max, quantas = 4) {
    const passo = passoBonito((max - min) / quantas || 1);
    const ini = Math.floor(min / passo) * passo;
    const fim = Math.ceil(max / passo) * passo;
    const marcas = [];
    for (let v = ini; v <= fim + passo / 2; v += passo) marcas.push(Number(v.toFixed(6)));
    return { min: ini, max: fim, marcas, casas: passo < 1 ? 1 : 0 };
  }

  // Marcas do eixo X em horários redondos (de hora em hora, de 6 em 6 h, por dia...)
  function marcasTempo(inicio, fim) {
    const H = 3600_000;
    const passos = [H, 2 * H, 3 * H, 6 * H, 12 * H, 24 * H, 48 * H, 7 * 24 * H];
    const passo = passos.find((p) => (fim - inicio) / p <= 6) ?? passos[passos.length - 1];
    const d = new Date(inicio);
    d.setMinutes(0, 0, 0);
    if (passo >= 24 * H) d.setHours(0);
    else d.setHours(Math.ceil(d.getHours() / (passo / H)) * (passo / H));
    const marcas = [];
    for (let t = d.getTime(); t <= fim; t += passo) if (t >= inicio) marcas.push(t);
    return marcas;
  }

  function desenharSerie(canvas, serie, metrica) {
    const { ctx, w, h } = prepararCanvas(canvas);
    const m = { esq: 48, dir: 12, topo: 10, base: 24 };
    const pontos = serie.pontos
      .filter((p) => p[metrica])
      .map((p) => ({ t: Date.parse(p.inicio), ...p[metrica] }));
    canvas._dica = null;
    if (!pontos.length) { mensagemVazia(ctx, w, h, "Sem dados no período"); return; }

    const fim = Date.now();
    const inicio = fim - estado.horas * 3600_000;
    let yMin = Math.min(...pontos.map((p) => p.min));
    let yMax = Math.max(...pontos.map((p) => p.max));
    const folga = Math.max((yMax - yMin) * 0.15, FOLGA_MINIMA[metrica] / 2);
    yMin -= folga; yMax += folga;

    const ref = estado.refs?.metricas?.[metrica] ?? {};
    const linhasRef = [
      [ref.atencao_acima, "atencao"], [ref.atencao_abaixo, "atencao"],
      [ref.critico_acima, "critico"], [ref.critico_abaixo, "critico"],
    ].filter(([v]) => v != null && v > yMin - (yMax - yMin) * 0.6 && v < yMax + (yMax - yMin) * 0.6);
    for (const [v] of linhasRef) { yMin = Math.min(yMin, v - folga / 2); yMax = Math.max(yMax, v + folga / 2); }
    const eixo = marcasY(yMin, yMax);
    yMin = eixo.min; yMax = eixo.max;

    const x = (t) => m.esq + ((t - inicio) / (fim - inicio)) * (w - m.esq - m.dir);
    const y = (v) => m.topo + (1 - (v - yMin) / (yMax - yMin)) * (h - m.topo - m.base);

    // grade e eixos
    ctx.strokeStyle = cor("grade"); ctx.fillStyle = cor("suave"); ctx.lineWidth = 1;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const v of eixo.marcas) {
      ctx.beginPath(); ctx.moveTo(m.esq, y(v)); ctx.lineTo(w - m.dir, y(v)); ctx.stroke();
      ctx.fillText(v.toLocaleString("pt-BR", { maximumFractionDigits: eixo.casas }), m.esq - 6, y(v));
    }
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (const t of marcasTempo(inicio, fim)) {
      ctx.fillText(rotuloTempo(t, estado.horas), Math.min(Math.max(x(t), m.esq + 18), w - m.dir - 18), h - m.base + 6);
    }

    // referências
    ctx.setLineDash([6, 5]);
    for (const [v, tipo] of linhasRef) {
      ctx.strokeStyle = cor(tipo);
      ctx.beginPath(); ctx.moveTo(m.esq, y(v)); ctx.lineTo(w - m.dir, y(v)); ctx.stroke();
    }
    ctx.setLineDash([]);

    // trechos contínuos (uma falha de sinal corta a linha em vez de inventar dados)
    const lacuna = serie.intervalo_s * 1000 * 2.5;
    const trechos = [];
    for (const p of pontos) {
      const atual = trechos[trechos.length - 1];
      if (atual && p.t - atual[atual.length - 1].t <= lacuna) atual.push(p); else trechos.push([p]);
    }
    const corLinha = cor("destaque");
    for (const trecho of trechos) {
      ctx.globalAlpha = 0.16; ctx.fillStyle = corLinha; ctx.beginPath();
      trecho.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p.max)) : ctx.moveTo(x(p.t), y(p.max))));
      for (let i = trecho.length - 1; i >= 0; i--) ctx.lineTo(x(trecho[i].t), y(trecho[i].min));
      ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;

      ctx.strokeStyle = corLinha; ctx.lineWidth = 2; ctx.beginPath();
      trecho.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p.media)) : ctx.moveTo(x(p.t), y(p.media))));
      if (trecho.length === 1) { ctx.arc(x(trecho[0].t), y(trecho[0].media), 2, 0, Math.PI * 2); }
      ctx.stroke();
    }

    canvas._dica = pontos.map((p) => ({
      x: x(p.t), y: y(p.media),
      texto: `${rotuloTempo(p.t, 1)} · média ${formatar(p.media, metrica)} (mín ${formatar(p.min, metrica)}, máx ${formatar(p.max, metrica)})`,
    }));
  }

  function desenharPadrao(canvas, horas) {
    const { ctx, w, h } = prepararCanvas(canvas);
    const m = { esq: 48, dir: 12, topo: 10, base: 24 };
    canvas._dica = null;
    const valores = horas.map((hh) => hh.co2_ppm);
    const comDados = valores.filter((v) => v != null);
    if (!comDados.length) { mensagemVazia(ctx, w, h, "Sem dados de CO₂ nos últimos 7 dias"); return; }

    const ref = estado.refs?.metricas?.co2_ppm ?? {};
    const eixo = marcasY(0, Math.max(...comDados, ref.atencao_acima ?? 0) * 1.05);
    const yMax = eixo.max;
    const largura = (w - m.esq - m.dir) / 24;
    const y = (v) => m.topo + (1 - v / yMax) * (h - m.topo - m.base);

    ctx.strokeStyle = cor("grade"); ctx.fillStyle = cor("suave");
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const v of eixo.marcas) {
      ctx.beginPath(); ctx.moveTo(m.esq, y(v)); ctx.lineTo(w - m.dir, y(v)); ctx.stroke();
      ctx.fillText(formatar(v, "co2_ppm"), m.esq - 6, y(v));
    }
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const dicas = [];
    horas.forEach((hh, i) => {
      const cx = m.esq + i * largura;
      if (i % 3 === 0) { ctx.fillStyle = cor("suave"); ctx.fillText(`${hh.hora}h`, cx + largura / 2, h - m.base + 6); }
      if (hh.co2_ppm == null) return;
      const n = nivel("co2_ppm", hh.co2_ppm);
      ctx.fillStyle = cor(n === "ok" ? "destaque" : n);
      ctx.fillRect(cx + 2, y(hh.co2_ppm), largura - 4, y(0) - y(hh.co2_ppm));
      dicas.push({ x: cx + largura / 2, y: y(hh.co2_ppm), texto: `${hh.hora}h · CO₂ médio ${formatar(hh.co2_ppm, "co2_ppm")} ppm` });
    });
    if (ref.atencao_acima != null) {
      ctx.setLineDash([6, 5]); ctx.strokeStyle = cor("atencao");
      ctx.beginPath(); ctx.moveTo(m.esq, y(ref.atencao_acima)); ctx.lineTo(w - m.dir, y(ref.atencao_acima)); ctx.stroke();
      ctx.setLineDash([]);
    }
    canvas._dica = dicas;
  }

  function ativarDicas(canvas) {
    const caixa = el("div", { class: "dica-grafico", hidden: "" });
    canvas.parentElement.append(caixa);
    canvas.addEventListener("mousemove", (ev) => {
      const pontos = canvas._dica;
      if (!pontos?.length) { caixa.hidden = true; return; }
      const r = canvas.getBoundingClientRect();
      const mx = ev.clientX - r.left;
      let melhor = pontos[0];
      for (const p of pontos) if (Math.abs(p.x - mx) < Math.abs(melhor.x - mx)) melhor = p;
      caixa.textContent = melhor.texto;
      caixa.style.left = `${canvas.offsetLeft + melhor.x}px`;
      caixa.style.top = `${canvas.offsetTop + melhor.y}px`;
      caixa.hidden = false;
    });
    canvas.addEventListener("mouseleave", () => { caixa.hidden = true; });
  }

  // ------------------------------------------------------------ ciclo principal

  function tratarErro(e) {
    if (e instanceof ErroHttp && e.status === 401) return; // formulário de token já apareceu
    console.error(e);
    mostrarErro(e.message || "Algo deu errado ao carregar os dados.");
  }

  let ocupado = false;
  async function atualizarTudo() {
    if (ocupado) return;
    ocupado = true;
    try {
      estado.salas = await api("/api/v1/dispositivos");
      if (!estado.salas.some((s) => s.id === estado.selecionado)) estado.selecionado = estado.salas[0]?.id ?? null;
      desenharSalas(estado.salas);
      await carregarDetalhe();
      limparErro();
      $("atualizado").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    } catch (e) {
      tratarErro(e);
      $("atualizado").textContent = "Falha na última atualização";
    } finally {
      ocupado = false;
    }
  }

  async function baixarCsv() {
    if (!estado.selecionado) return;
    try {
      const blob = await api(`/api/v1/dispositivos/${encodeURIComponent(estado.selecionado)}/exportar.csv?horas=${estado.horas}`, { comoArquivo: true });
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: `aurora-${estado.selecionado}.csv` });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e) { tratarErro(e); }
  }

  async function iniciar() {
    for (const id of ["g-co2_ppm", "g-temperatura_c", "g-umidade_pct", "g-padrao"]) ativarDicas($(id));
    $("btn-atualizar").addEventListener("click", atualizarTudo);
    $("btn-csv").addEventListener("click", baixarCsv);
    $("periodo").addEventListener("change", (ev) => {
      estado.horas = Number(ev.target.value) || 24;
      carregarDetalhe().catch(tratarErro);
    });
    $("form-token").addEventListener("submit", (ev) => {
      ev.preventDefault();
      estado.token = $("campo-token").value.trim();
      gravarArmazenado(sessionStorage, "aurora-token", estado.token);
      $("campo-token").value = "";
      $("form-token").hidden = true;
      atualizarTudo();
    });
    let espera;
    window.addEventListener("resize", () => { clearTimeout(espera); espera = setTimeout(desenharDetalhe, 150); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) atualizarTudo(); });
    setInterval(() => { if (!document.hidden) atualizarTudo(); }, ATUALIZAR_A_CADA_MS);

    try {
      estado.refs = await api("/api/v1/referencias");
      $("aviso").textContent = estado.refs.aviso;
    } catch (e) { tratarErro(e); }
    await atualizarTudo();
  }

  document.addEventListener("DOMContentLoaded", iniciar);
})();
