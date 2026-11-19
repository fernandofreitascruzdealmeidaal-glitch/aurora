"use strict";
// Site da Aurora — sem bibliotecas externas. Todo texto vindo do servidor entra com
// textContent (nunca innerHTML), e só links http/https viram links clicáveis.

(() => {
  const $ = (id) => document.getElementById(id);
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const MESES_LONGOS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
    "setembro", "outubro", "novembro", "dezembro"];
  let catalogo = { tipos: {}, regioes: [] };

  // ------------------------------------------------------------ utilidades

  function el(tag, attrs = {}, ...filhos) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const f of filhos.flat()) if (f != null && f !== false) e.append(f instanceof Node ? f : String(f));
    return e;
  }

  function linkSeguro(url) {
    try {
      const u = new URL(url);
      return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
    } catch { return null; }
  }

  function dataBR(iso) {
    if (!iso) return "";
    const [a, m, d] = iso.split("-").map(Number);
    return `${d} de ${MESES[m - 1]}. de ${a}`;
  }

  function periodo(item) {
    if (!item.inicio) return null;
    if (!item.data_confirmada) {
      const [a, m] = item.inicio.split("-").map(Number);
      return `Previsto para ${MESES_LONGOS[m - 1]} de ${a}`;
    }
    if (item.fim && item.fim !== item.inicio) return `${dataBR(item.inicio)} a ${dataBR(item.fim)}`;
    return dataBR(item.inicio);
  }

  async function api(caminho) {
    const controle = new AbortController();
    const relogio = setTimeout(() => controle.abort(), 15000);
    try {
      const r = await fetch(caminho, { signal: controle.signal });
      if (!r.ok) {
        let msg = `Erro ${r.status}`;
        try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* sem JSON */ }
        throw new Error(msg);
      }
      return await r.json();
    } catch (e) {
      if (e.name === "AbortError") throw new Error("O servidor demorou demais para responder.");
      if (e instanceof TypeError) throw new Error("Sem conexão com o servidor da Aurora.");
      throw e;
    } finally {
      clearTimeout(relogio);
    }
  }

  function mostrarErro(msg) { const e = $("erro"); e.textContent = msg; e.hidden = !msg; }

  // ------------------------------------------------------------ componentes

  function cartaoItem(item) {
    const url = linkSeguro(item.url);
    const titulo = url
      ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, item.titulo)
      : item.titulo;
    const onde = [item.local, item.cidade, item.regiao].filter(Boolean).join(" · ");
    const quando = periodo(item);
    const conferido = item.verificado_em ? ` · conferido em ${dataBR(item.verificado_em)}` : "";
    const publicado = item.tipo === "noticia" && item.publicado_em
      ? ` · publicado em ${new Date(item.publicado_em * 1000).toLocaleDateString("pt-BR")}` : "";
    return el("article", { class: "item" },
      el("div", { class: "etiquetas" },
        el("span", { class: "etiqueta" }, item.tipo_nome),
        item.gratuito ? el("span", { class: "etiqueta gratis" }, "Gratuito") : null,
        item.inicio && !item.data_confirmada ? el("span", { class: "etiqueta previsto" }, "Data prevista") : null),
      el("h3", {}, titulo),
      el("div", { class: "meta" },
        quando ? el("span", { class: "quando" }, quando) : null,
        item.recorrencia ? el("span", { class: "quando" }, item.recorrencia) : null,
        onde ? el("span", { class: "onde" }, onde) : null,
        item.instituicao ? el("span", { class: "quem" }, item.instituicao) : null,
        item.publico ? el("span", { class: "para" }, item.publico) : null),
      item.resumo ? el("p", { class: "resumo" }, item.resumo) : null,
      el("div", { class: "rodape-item" }, `Fonte: ${item.fonte || "equipe Aurora"}${publicado}${conferido}`));
  }

  function grade(itens, vazio) {
    if (!itens.length) return el("p", { class: "vazio" }, vazio);
    return el("div", { class: "grade" }, itens.map(cartaoItem));
  }

  function listaDatas(datas) {
    return el("ul", { class: "datas" }, datas.map((d) => {
      const [, m, dia] = d.data.split("-").map(Number);
      const falta = d.faltam_dias === 0 ? "é hoje!" : d.faltam_dias === 1 ? "amanhã" : `em ${d.faltam_dias} dias`;
      return el("li", { class: "data" },
        el("span", { class: "calendario", "aria-hidden": "true" },
          el("span", { class: "mes" }, MESES[m - 1]), el("span", { class: "dia" }, dia)),
        el("div", {},
          el("span", { class: "nome" }, d.nome), " ", el("span", { class: "falta" }, `— ${falta}`),
          d.descricao ? el("p", {}, d.descricao) : null));
    }));
  }

  // ------------------------------------------------------------ páginas

  async function paginaInicio(vista) {
    const d = await api("/api/v1/conteudo/inicio");
    const c = d.contagens || {};
    const total = (...tipos) => tipos.reduce((s, t) => s + (c[t] || 0), 0);
    const dica = d.dica_do_dia;
    vista.replaceChildren(
      el("section", { class: "destaque" },
        el("div", { class: "painel dica-dia" },
          el("span", { class: "rotulo" }, "Dica do dia"),
          dica ? el("h3", {}, dica.titulo) : null,
          dica ? el("p", {}, dica.texto) : el("p", {}, "Em breve."),
          el("p", {}, el("a", { href: "#/dicas" }, "Ver todas as dicas →")),
          el("div", { class: "numeros" },
            el("div", { class: "numero" }, el("strong", {}, total("evento", "feira_ciencias", "feira_sustentavel", "parque")), el("span", {}, "eventos e feiras")),
            el("div", { class: "numero" }, el("strong", {}, total("voluntariado", "curso", "vaga")), el("span", {}, "oportunidades")),
            el("div", { class: "numero" }, el("strong", {}, total("noticia")), el("span", {}, "notícias")))),
        el("div", { class: "painel" },
          el("div", { class: "secao-topo" }, el("h2", {}, "Próximas datas"), el("a", { class: "ver-mais", href: "#/datas" }, "Calendário completo")),
          d.proximas_datas.length ? listaDatas(d.proximas_datas) : el("p", { class: "vazio" }, "Nenhuma data ambiental nos próximos dias."))),
      el("div", { class: "secao-topo" }, el("h2", {}, "Próximos eventos"), el("a", { class: "ver-mais", href: "#/agenda" }, "Agenda completa")),
      grade(d.proximos_eventos, "Nenhum evento com data marcada por enquanto."),
      el("div", { class: "secao-topo" }, el("h2", {}, "Para participar"), el("a", { class: "ver-mais", href: "#/oportunidades" }, "Todas as oportunidades")),
      grade(d.oportunidades, "Nenhuma oportunidade cadastrada."),
      el("div", { class: "secao-topo" }, el("h2", {}, "Últimas notícias"), el("a", { class: "ver-mais", href: "#/noticias" }, "Todas as notícias")),
      grade(d.noticias, "Nenhuma notícia aprovada ainda."));
  }

  function paginaLista(config) {
    return async (vista) => {
      const estado = { tipos: new Set(config.tipos), regiao: "", busca: "", pagina: 1 };
      const POR_PAGINA = 24;
      const resultado = el("div", {}, el("p", { class: "carregando" }, "Carregando…"));
      const busca = el("input", { type: "search", placeholder: "Buscar (ex.: plantio, UFMG, Cerrado)", "aria-label": "Buscar" });
      const regiao = el("select", { "aria-label": "Região" },
        el("option", { value: "" }, "Todas as regiões"),
        catalogo.regioes.filter((r) => !["Todo o estado", "On-line", "Nacional"].includes(r))
          .map((r) => el("option", { value: r }, r)));
      const chips = config.tipos.length > 1 ? el("div", { class: "chips", role: "group", "aria-label": "Tipos" },
        config.tipos.map((t) => {
          const b = el("button", { type: "button", class: "chip", "aria-pressed": "true" }, catalogo.tipos[t] || t);
          b.addEventListener("click", () => {
            if (estado.tipos.has(t) && estado.tipos.size > 1) estado.tipos.delete(t); else estado.tipos.add(t);
            for (const [i, tt] of config.tipos.entries()) chips.children[i].setAttribute("aria-pressed", String(estado.tipos.has(tt)));
            estado.pagina = 1; carregar();
          });
          return b;
        })) : null;

      async function carregar() {
        const p = new URLSearchParams({ tipo: [...estado.tipos].join(","), quando: config.quando,
          limite: String(POR_PAGINA), pagina: String(estado.pagina) });
        if (estado.regiao) p.set("regiao", estado.regiao);
        if (estado.busca) p.set("busca", estado.busca);
        try {
          const d = await api(`/api/v1/conteudo/itens?${p}`);
          const paginas = Math.max(1, Math.ceil(d.total / POR_PAGINA));
          const nav = paginas > 1 ? el("div", { class: "paginacao" },
            el("button", { type: "button", disabled: estado.pagina <= 1, onclick: null }, "← Anteriores"),
            el("span", {}, `Página ${estado.pagina} de ${paginas}`),
            el("button", { type: "button", disabled: estado.pagina >= paginas }, "Próximos →")) : null;
          if (nav) {
            nav.children[0].addEventListener("click", () => { estado.pagina--; carregar(); });
            nav.children[2].addEventListener("click", () => { estado.pagina++; carregar(); });
          }
          resultado.replaceChildren(
            el("p", { class: "aviso-curadoria" }, `${d.total} resultado(s).`),
            grade(d.itens, config.vazio), nav);
          mostrarErro("");
        } catch (e) { mostrarErro(e.message); }
      }

      let espera;
      busca.addEventListener("input", () => {
        clearTimeout(espera);
        espera = setTimeout(() => { estado.busca = busca.value.trim(); estado.pagina = 1; carregar(); }, 300);
      });
      regiao.addEventListener("change", () => { estado.regiao = regiao.value; estado.pagina = 1; carregar(); });

      vista.replaceChildren(
        el("h2", {}, config.titulo),
        config.intro ? el("p", { class: "aviso-curadoria" }, config.intro) : null,
        el("div", { class: "filtros" }, busca, regiao, chips),
        resultado);
      await carregar();
    };
  }

  async function paginaDatas(vista) {
    const datas = await api("/api/v1/conteudo/datas?dias=366");
    const porMes = new Map();
    for (const d of datas) {
      const [a, m] = d.data.split("-").map(Number);
      const chave = `${MESES_LONGOS[m - 1]} de ${a}`;
      if (!porMes.has(chave)) porMes.set(chave, []);
      porMes.get(chave).push(d);
    }
    vista.replaceChildren(
      el("h2", {}, "Datas importantes para o meio ambiente"),
      el("p", { class: "aviso-curadoria" }, "Os próximos 12 meses. Boas oportunidades para campanhas na escola, mutirões e trabalhos."),
      ...[...porMes.entries()].map(([mes, lista]) => el("section", { class: "painel" },
        el("h2", {}, mes.charAt(0).toUpperCase() + mes.slice(1)), listaDatas(lista))));
  }

  async function paginaDicas(vista) {
    const dicas = await api("/api/v1/conteudo/dicas");
    const porCategoria = new Map();
    for (const d of dicas) {
      if (!porCategoria.has(d.categoria)) porCategoria.set(d.categoria, []);
      porCategoria.get(d.categoria).push(d);
    }
    vista.replaceChildren(
      el("h2", {}, "Como ser mais sustentável"),
      el("p", { class: "aviso-curadoria" }, "Atitudes simples para casa e para a escola."),
      ...[...porCategoria.entries()].map(([cat, lista]) => [
        el("h2", {}, cat),
        el("div", { class: "dicas" }, lista.map((d) => el("div", { class: "painel dica" }, el("h3", {}, d.titulo), el("p", {}, d.texto)))),
      ]).flat());
  }

  // ------------------------------------------------------------ rotas

  const ROTAS = {
    inicio: paginaInicio,
    agenda: paginaLista({
      titulo: "Agenda", quando: "futuros",
      tipos: ["evento", "feira_ciencias", "feira_sustentavel", "parque"],
      intro: "Eventos, feiras e atividades em parques. Itens sem data são programas permanentes ou que se repetem.",
      vazio: "Nada encontrado com esses filtros.",
    }),
    feiras: paginaLista({
      titulo: "Feiras de ciências e mostras", quando: "futuros", tipos: ["feira_ciencias"],
      intro: "Feiras e programas de iniciação científica em universidades, institutos federais, CEFET, SENAI e no BH-TEC — lugares para apresentar o seu trabalho.",
      vazio: "Nenhuma feira encontrada.",
    }),
    oportunidades: paginaLista({
      titulo: "Voluntariado, cursos e vagas", quando: "futuros", tipos: ["voluntariado", "curso", "vaga"],
      intro: "Confira sempre a idade mínima: algumas atividades são só para adultos. Para menores de 16 anos, a única forma de trabalho permitida é como jovem aprendiz (a partir dos 14).",
      vazio: "Nenhuma oportunidade encontrada.",
    }),
    noticias: paginaLista({
      titulo: "Notícias", quando: "todos", tipos: ["noticia"],
      intro: "Notícias coletadas de fontes confiáveis e aprovadas pela equipe antes de aparecer aqui.",
      vazio: "Nenhuma notícia encontrada.",
    }),
    datas: paginaDatas,
    dicas: paginaDicas,
  };

  async function navegar() {
    const rota = (location.hash.replace(/^#\//, "").split("?")[0]) || "inicio";
    const pagina = ROTAS[rota] || ROTAS.inicio;
    for (const a of document.querySelectorAll(".abas a[data-rota]")) {
      if (a.dataset.rota === rota) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    const vista = $("vista");
    vista.replaceChildren(el("p", { class: "carregando" }, "Carregando…"));
    mostrarErro("");
    try {
      await pagina(vista);
    } catch (e) {
      vista.replaceChildren();
      mostrarErro(e.message || "Não foi possível carregar.");
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    try { catalogo = await api("/api/v1/conteudo/catalogo"); } catch (e) { mostrarErro(e.message); }
    window.addEventListener("hashchange", () => { navegar(); $("conteudo").focus({ preventScroll: true }); });
    navegar();
  });
})();
