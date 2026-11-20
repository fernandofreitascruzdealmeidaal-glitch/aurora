"use strict";
// Área da equipe: curadoria do conteúdo. Exige o token AURORA_TOKEN_ADMIN.

(() => {
  const $ = (id) => document.getElementById(id);
  let token = (() => { try { return sessionStorage.getItem("aurora-equipe"); } catch { return null; } })();
  let catalogo = { tipos: {}, regioes: [] };

  function el(tag, attrs = {}, ...filhos) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v; else e.setAttribute(k, v === true ? "" : v);
    }
    for (const f of filhos.flat()) if (f != null && f !== false) e.append(f instanceof Node ? f : String(f));
    return e;
  }
  const linkSeguro = (u) => { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; } };
  const mostrarErro = (m) => { $("erro").textContent = m || ""; $("erro").hidden = !m; };

  function guardarToken(t) {
    token = t;
    try { t ? sessionStorage.setItem("aurora-equipe", t) : sessionStorage.removeItem("aurora-equipe"); } catch { /* ok */ }
  }

  async function api(metodo, caminho, corpo) {
    const r = await fetch(caminho, {
      method: metodo,
      headers: { Authorization: `Bearer ${token || ""}`, ...(corpo ? { "Content-Type": "application/json" } : {}) },
      body: corpo ? JSON.stringify(corpo) : undefined,
    });
    if (r.status === 401) { guardarToken(null); mostrarLogin(); throw new Error("Token inválido ou expirado."); }
    if (r.status === 204) return null;
    let dados = null;
    try { dados = await r.json(); } catch { /* sem corpo */ }
    if (!r.ok) {
      const d = dados && dados.detail;
      const msg = typeof d === "string" ? d
        : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0] || ""}: ${x.msg}`).join("; ")
        : `Erro ${r.status}`;
      throw new Error(msg);
    }
    return dados;
  }

  function opcoes(select, pares, vazio) {
    select.replaceChildren(...(vazio ? [el("option", { value: "" }, vazio)] : []),
      ...pares.map(([v, rotulo]) => el("option", { value: v }, rotulo)));
  }

  function mostrarLogin() { $("entrar").hidden = false; $("area").hidden = true; $("token").focus(); }

  // ------------------------------------------------------------ pendentes

  function cartaoPendente(item) {
    const tipo = el("select", { "aria-label": "Tipo" });
    opcoes(tipo, Object.entries(catalogo.tipos));
    tipo.value = item.tipo;
    const regiao = el("select", { "aria-label": "Região" });
    opcoes(regiao, catalogo.regioes.map((r) => [r, r]), "(sem região)");
    regiao.value = item.regiao || "";
    const url = linkSeguro(item.url);
    const aprovar = el("button", { type: "button", class: "botao-principal" }, "Aprovar");
    const rejeitar = el("button", { type: "button" }, "Rejeitar");
    const cartao = el("article", { class: "item" },
      el("h3", {}, url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, item.titulo) : item.titulo),
      el("div", { class: "meta" }, el("span", {}, `Fonte: ${item.fonte || "—"}`),
        item.publicado_em ? el("span", {}, new Date(item.publicado_em * 1000).toLocaleDateString("pt-BR")) : null),
      item.resumo ? el("p", { class: "resumo" }, item.resumo) : null,
      el("div", { class: "acoes" }, tipo, regiao, aprovar, rejeitar));
    const decidir = async (status) => {
      aprovar.disabled = rejeitar.disabled = true;
      try {
        await api("PATCH", `/api/v1/equipe/itens/${item.id}`, { status, tipo: tipo.value, regiao: regiao.value });
        cartao.remove();
        atualizarContador(-1);
      } catch (e) { mostrarErro(e.message); aprovar.disabled = rejeitar.disabled = false; }
    };
    aprovar.addEventListener("click", () => decidir("aprovado"));
    rejeitar.addEventListener("click", () => decidir("rejeitado"));
    return cartao;
  }

  let nPendentes = 0;
  function atualizarContador(delta = 0) {
    nPendentes += delta;
    $("n-pendentes").textContent = `(${nPendentes})`;
  }

  async function carregarPendentes() {
    const d = await api("GET", "/api/v1/equipe/itens?status=pendente&limite=50");
    nPendentes = d.total; atualizarContador();
    $("pendentes").replaceChildren(d.itens.length
      ? el("div", { class: "grade" }, d.itens.map(cartaoPendente))
      : el("p", { class: "vazio" }, "Nada esperando aprovação. 🎉"));
  }

  // ------------------------------------------------------------ publicados

  async function carregarAprovados() {
    const d = await api("GET", "/api/v1/equipe/itens?status=aprovado&limite=30");
    const linhas = d.itens.map((i) => {
      const despublicar = el("button", { type: "button" }, "Despublicar");
      const excluir = el("button", { type: "button" }, "Excluir");
      despublicar.addEventListener("click", async () => {
        try { await api("PATCH", `/api/v1/equipe/itens/${i.id}`, { status: "pendente" }); await recarregar(); } catch (e) { mostrarErro(e.message); }
      });
      excluir.addEventListener("click", async () => {
        if (!window.confirm(`Excluir de vez "${i.titulo}"?`)) return;
        try { await api("DELETE", `/api/v1/equipe/itens/${i.id}`); await recarregar(); } catch (e) { mostrarErro(e.message); }
      });
      return el("tr", {}, el("td", {}, i.titulo), el("td", {}, i.tipo_nome), el("td", {}, i.origem),
        el("td", {}, el("div", { class: "acoes" }, despublicar, excluir)));
    });
    $("aprovados").replaceChildren(el("table", { class: "tabela" },
      el("thead", {}, el("tr", {}, el("th", {}, "Título"), el("th", {}, "Tipo"), el("th", {}, "Origem"), el("th", {}, "Ações"))),
      el("tbody", {}, linhas)));
  }

  // ------------------------------------------------------------ fontes

  async function carregarFontes() {
    const d = await api("GET", "/api/v1/equipe/resumo");
    const c = d.chat;
    const dolar = (v) => `US$ ${v.toFixed(2).replace(".", ",")}`;
    $("chat-uso").replaceChildren(c.ligado
      ? el("p", {}, `Ligado (modelo ${c.modelo}). Hoje: ${c.hoje.mensagens} de ${c.limite_dia} mensagens, cerca de ${dolar(c.hoje.custo_estimado_usd)}. `,
        `Neste mês: ${c.mes.mensagens} mensagens, cerca de ${dolar(c.mes.custo_estimado_usd)}. A conta oficial fica no Console da Anthropic.`)
      : el("p", {}, "Desligado. Para ligar, cole a chave da API da Anthropic no painel da Aurora, no computador onde ela roda."));
    const linhas = d.fontes.map((f) => {
      const alternar = el("button", { type: "button" }, f.ativa ? "Desativar" : "Ativar");
      alternar.addEventListener("click", async () => {
        try { await api("PATCH", `/api/v1/equipe/fontes/${f.id}`, { ativa: !f.ativa }); await carregarFontes(); } catch (e) { mostrarErro(e.message); }
      });
      const quando = f.ultima_coleta ? new Date(f.ultima_coleta * 1000).toLocaleString("pt-BR") : "nunca";
      return el("tr", {},
        el("td", {}, f.nome, el("br"), el("small", {}, f.url)),
        el("td", {}, f.ativa ? "ativa" : "desativada"),
        el("td", {}, quando, f.ultimo_erro ? el("div", { class: "aviso-curadoria" }, `⚠ ${f.ultimo_erro}`) : null),
        el("td", {}, f.observacao || ""),
        el("td", {}, alternar));
    });
    $("fontes").replaceChildren(el("table", { class: "tabela" },
      el("thead", {}, el("tr", {}, ["Fonte", "Situação", "Última coleta", "Observação", ""].map((t) => el("th", {}, t)))),
      el("tbody", {}, linhas)));
  }

  // ------------------------------------------------------------ formulários

  function lerFormulario(form) {
    const dados = {};
    for (const campo of form.elements) {
      if (!campo.name) continue;
      if (campo.type === "checkbox") dados[campo.name] = campo.checked;
      else if (campo.value.trim() !== "") dados[campo.name] = campo.value.trim();
    }
    return dados;
  }

  async function recarregar() {
    await Promise.all([carregarPendentes(), carregarAprovados(), carregarFontes()]);
  }

  async function iniciarArea() {
    $("entrar").hidden = true;
    $("area").hidden = false;
    try {
      await recarregar();
      mostrarErro("");
    } catch (e) { mostrarErro(e.message); }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    try {
      catalogo = await (await fetch("/api/v1/conteudo/catalogo")).json();
    } catch { mostrarErro("Sem conexão com o servidor."); return; }
    opcoes($("novo").elements.tipo, Object.entries(catalogo.tipos).filter(([t]) => t !== "noticia").concat([["noticia", catalogo.tipos.noticia]]));
    opcoes($("novo").elements.regiao, catalogo.regioes.map((r) => [r, r]), "(escolha)");
    opcoes($("nova-fonte").elements.regiao_padrao, catalogo.regioes.map((r) => [r, r]), "(nenhuma)");

    $("entrar").addEventListener("submit", (ev) => {
      ev.preventDefault();
      guardarToken($("token").value.trim());
      $("token").value = "";
      iniciarArea();
    });
    $("sair").addEventListener("click", () => { guardarToken(null); mostrarLogin(); });

    $("novo").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const dados = lerFormulario(ev.target);
      dados.verificado_em = new Date().toISOString().slice(0, 10);
      try {
        await api("POST", "/api/v1/equipe/itens", dados);
        ev.target.reset();
        $("novo-ok").textContent = "Publicado!";
        setTimeout(() => { $("novo-ok").textContent = ""; }, 4000);
        await carregarAprovados();
        mostrarErro("");
      } catch (e) { mostrarErro(e.message); }
    });

    $("testar").addEventListener("click", async () => {
      const url = $("nova-fonte").elements.url.value.trim();
      $("teste-resultado").textContent = "Testando…";
      try {
        const r = await api("POST", "/api/v1/equipe/fontes/testar", { url });
        $("teste-resultado").textContent = r.ok
          ? `Funciona: ${r.total} itens, ${r.sobre_sustentabilidade} sobre sustentabilidade. Ex.: ${r.amostra.map((a) => a.titulo).slice(0, 2).join(" | ")}`
          : `Não funcionou: ${r.erro}`;
      } catch (e) { $("teste-resultado").textContent = e.message; }
    });

    $("nova-fonte").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api("POST", "/api/v1/equipe/fontes", lerFormulario(ev.target));
        ev.target.reset();
        await carregarFontes();
        mostrarErro("");
      } catch (e) { mostrarErro(e.message); }
    });

    $("coletar").addEventListener("click", async () => {
      $("coletar").disabled = true;
      $("coleta-ok").textContent = "Coletando… (pode levar alguns segundos)";
      try {
        const r = await api("POST", "/api/v1/equipe/coletar");
        const novos = r.reduce((s, x) => s + x.novos, 0);
        const erros = r.filter((x) => x.erro).length;
        $("coleta-ok").textContent = `${novos} novo(s) para aprovar${erros ? `; ${erros} fonte(s) com erro` : ""}.`;
        await recarregar();
      } catch (e) { mostrarErro(e.message); $("coleta-ok").textContent = ""; }
      $("coletar").disabled = false;
    });

    if (token) iniciarArea(); else mostrarLogin();
  });
})();
