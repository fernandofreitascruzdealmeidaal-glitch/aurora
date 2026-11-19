"use strict";
// Service worker da Aurora: deixa o app instalável e abre o que já foi visto mesmo sem internet.
//
// Regras (pensadas para nunca mostrar dado errado nem guardar coisa sensível):
//  - Páginas e arquivos do site: abre da memória e atualiza em segundo plano.
//  - Conteúdo público (/api/v1/conteudo/...): tenta a internet primeiro; sem rede, usa a última
//    cópia salva e avisa o app (cabeçalho X-Aurora-Offline) para mostrar "você está sem internet".
//  - Área da equipe, sensores e qualquer envio (POST/PATCH/DELETE): NUNCA passa pela memória.

const VERSAO = "aurora-v2";
const CASCA = `${VERSAO}-casca`;
const DADOS = `${VERSAO}-dados`;
const ARQUIVOS_DA_CASCA = [
  "/",
  "/static/site.css",
  "/static/site.js",
  "/static/manifest.webmanifest",
  "/static/icones/icone-192.png",
  "/static/icones/icone-512.png",
  "/static/icones/favicon-48.png",
];
const MAX_RESPOSTAS_SALVAS = 80;

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(CASCA).then((c) => c.addAll(ARQUIVOS_DA_CASCA)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil((async () => {
    for (const nome of await caches.keys()) {
      if (!nome.startsWith(VERSAO)) await caches.delete(nome); // apaga versões antigas
    }
    await self.clients.claim();
  })());
});

function podeGuardar(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.startsWith("/api/v1/conteudo/")) return true;
  return url.pathname === "/" || url.pathname.startsWith("/static/");
}

async function limitar(cache) {
  const chaves = await cache.keys();
  for (const chave of chaves.slice(0, Math.max(0, chaves.length - MAX_RESPOSTAS_SALVAS))) {
    await cache.delete(chave);
  }
}

async function redePrimeiro(pedido) {
  const cache = await caches.open(DADOS);
  try {
    const resposta = await fetch(pedido);
    if (resposta.ok) {
      await cache.put(pedido, resposta.clone());
      limitar(cache);
    }
    return resposta;
  } catch (erro) {
    const salva = await cache.match(pedido);
    if (!salva) throw erro;
    const cabecalhos = new Headers(salva.headers);
    cabecalhos.set("X-Aurora-Offline", "1");
    return new Response(await salva.blob(), { status: salva.status, headers: cabecalhos });
  }
}

async function memoriaPrimeiro(pedido) {
  const cache = await caches.open(CASCA);
  const salva = await cache.match(pedido, { ignoreSearch: pedido.mode === "navigate" });
  const atualizar = fetch(pedido).then((resposta) => {
    if (resposta.ok) cache.put(pedido, resposta.clone());
    return resposta;
  }).catch(() => null);
  return salva || (await atualizar) || (pedido.mode === "navigate" ? cache.match("/") : Response.error());
}

self.addEventListener("fetch", (evento) => {
  const pedido = evento.request;
  if (pedido.method !== "GET") return; // envios vão direto para a rede
  const url = new URL(pedido.url);
  if (!podeGuardar(url)) return; // equipe, sensores, salas: sempre rede
  if (url.pathname.startsWith("/api/")) {
    evento.respondWith(redePrimeiro(pedido));
  } else {
    evento.respondWith(memoriaPrimeiro(pedido));
  }
});
