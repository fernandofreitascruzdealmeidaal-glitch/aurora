package br.aurora.sustentabilidade;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.TextUtils;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * App Android da Aurora: mostra o site do servidor da Aurora numa tela cheia.
 *
 * - Na primeira vez, pergunta o endereço do servidor (ex.: http://192.168.0.10:8000) e testa
 *   antes de salvar.
 * - Links para outros sites (as fontes das notícias) abrem no navegador do celular.
 * - Sem internet, mostra o que já estava guardado; se não houver nada, uma tela explicando.
 */
public class MainActivity extends Activity {

    private static final String PREFERENCIAS = "aurora";
    private static final String CHAVE_SERVIDOR = "servidor";
    private static final int TEMPO_LIMITE_MS = 6000;

    private WebView web;
    private String servidor;
    private final ExecutorService fundo = Executors.newSingleThreadExecutor();
    private final Handler principal = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle estado) {
        super.onCreate(estado);
        web = new WebView(this);
        setContentView(web);

        WebSettings ajustes = web.getSettings();
        ajustes.setJavaScriptEnabled(true);
        ajustes.setDomStorageEnabled(true);
        ajustes.setAllowFileAccess(false);
        ajustes.setAllowContentAccess(false);
        ajustes.setUserAgentString(ajustes.getUserAgentString() + " AuroraApp/0.2");

        web.addJavascriptInterface(new Ponte(), "AuroraApp");
        web.setWebViewClient(new Cliente());
        web.setWebChromeClient(new WebChromeClient());

        servidor = getSharedPreferences(PREFERENCIAS, MODE_PRIVATE)
                .getString(CHAVE_SERVIDOR, BuildConfig.SERVIDOR_PADRAO);

        if (estado != null && web.restoreState(estado) != null) {
            return;
        }
        if (TextUtils.isEmpty(servidor)) {
            pedirServidor();
        } else {
            carregar();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle estado) {
        super.onSaveInstanceState(estado);
        web.saveState(estado);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        fundo.shutdownNow();
        web.destroy();
        super.onDestroy();
    }

    // ------------------------------------------------------------------ servidor

    private boolean temInternet() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return true;
        NetworkCapabilities cap = cm.getNetworkCapabilities(cm.getActiveNetwork());
        return cap != null && cap.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void carregar() {
        // Sem rede, usa o que estiver guardado no aparelho em vez de mostrar erro.
        web.getSettings().setCacheMode(
                temInternet() ? WebSettings.LOAD_DEFAULT : WebSettings.LOAD_CACHE_ELSE_NETWORK);
        web.loadUrl(servidor + "/");
    }

    /** "192.168.0.10:8000/" vira "http://192.168.0.10:8000". Devolve null se for inválido. */
    static String normalizar(String texto) {
        if (texto == null) return null;
        String s = texto.trim();
        if (s.isEmpty()) return null;
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        Uri u = Uri.parse(s);
        if (u.getHost() == null || u.getHost().isEmpty()) return null;
        return s;
    }

    private void pedirServidor() {
        final EditText campo = new EditText(this);
        campo.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        campo.setHint("http://192.168.0.10:8000");
        if (!TextUtils.isEmpty(servidor)) campo.setText(servidor);

        final AlertDialog dialogo = new AlertDialog.Builder(this)
                .setTitle("Endereço do servidor Aurora")
                .setMessage("Digite o endereço que aparece no computador onde a Aurora está ligada.")
                .setView(campo)
                .setCancelable(!TextUtils.isEmpty(servidor))
                .setPositiveButton("Conectar", null)
                .create();

        dialogo.setOnShowListener(d -> {
            Button conectar = dialogo.getButton(AlertDialog.BUTTON_POSITIVE);
            conectar.setOnClickListener(v -> {
                final String endereco = normalizar(campo.getText().toString());
                if (endereco == null) {
                    campo.setError("Endereço inválido");
                    return;
                }
                conectar.setEnabled(false);
                conectar.setText("Testando…");
                testar(endereco, ok -> {
                    conectar.setEnabled(true);
                    conectar.setText("Conectar");
                    if (ok) {
                        servidor = endereco;
                        getSharedPreferences(PREFERENCIAS, MODE_PRIVATE).edit()
                                .putString(CHAVE_SERVIDOR, servidor).apply();
                        dialogo.dismiss();
                        web.clearHistory();
                        carregar();
                    } else {
                        campo.setError("Não encontrei a Aurora nesse endereço. "
                                + "O servidor está ligado e na mesma rede?");
                    }
                });
            });
        });
        dialogo.show();
    }

    private interface Resposta {
        void ao(boolean ok);
    }

    /** Confere se o endereço responde em /saude antes de salvar. */
    private void testar(final String endereco, final Resposta resposta) {
        fundo.execute(() -> {
            boolean ok = false;
            HttpURLConnection conexao = null;
            try {
                conexao = (HttpURLConnection) new URL(endereco + "/saude").openConnection();
                conexao.setConnectTimeout(TEMPO_LIMITE_MS);
                conexao.setReadTimeout(TEMPO_LIMITE_MS);
                ok = conexao.getResponseCode() == 200;
            } catch (Exception ignorada) {
                ok = false;
            } finally {
                if (conexao != null) conexao.disconnect();
            }
            final boolean resultado = ok;
            principal.post(() -> resposta.ao(resultado));
        });
    }

    private void mostrarErro(String detalhe) {
        String html = "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                + "<style>body{font-family:sans-serif;background:#f6f4ee;color:#1d2a22;padding:28px;}"
                + "h1{color:#1f6b45;font-size:1.4rem}button{display:block;width:100%;margin:12px 0;"
                + "padding:14px;font-size:1rem;border-radius:12px;border:1px solid #1f6b45;"
                + "background:#fff;color:#1f6b45}button.p{background:#1f6b45;color:#fff}"
                + "small{color:#5c6b61}</style></head><body>"
                + "<h1>Não consegui abrir a Aurora</h1>"
                + "<p>Confira se o celular está na internet (ou no Wi-Fi da escola) "
                + "e se o computador do servidor está ligado.</p>"
                + "<button class='p' onclick='AuroraApp.tentarDeNovo()'>Tentar de novo</button>"
                + "<button onclick='AuroraApp.trocarServidor()'>Trocar o endereço do servidor</button>"
                + "<p><small>Servidor: " + TextUtils.htmlEncode(servidor == null ? "" : servidor)
                + "<br>Detalhe: " + TextUtils.htmlEncode(detalhe == null ? "" : detalhe)
                + "</small></p></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    // ------------------------------------------------------------------ navegação

    private boolean ehDoServidor(Uri u) {
        if (servidor == null) return false;
        Uri s = Uri.parse(servidor);
        return TextUtils.equals(u.getScheme(), s.getScheme())
                && TextUtils.equals(u.getHost(), s.getHost())
                && u.getPort() == s.getPort();
    }

    private class Cliente extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest pedido) {
            Uri u = pedido.getUrl();
            if (ehDoServidor(u)) return false;
            String esquema = u.getScheme();
            if ("http".equals(esquema) || "https".equals(esquema) || "mailto".equals(esquema)) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException e) {
                    Toast.makeText(MainActivity.this, "Nenhum app para abrir esse link",
                            Toast.LENGTH_SHORT).show();
                }
            }
            return true; // nunca navega para fora do servidor dentro do app
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest pedido, WebResourceError erro) {
            if (pedido.isForMainFrame()) {
                mostrarErro(String.valueOf(erro.getDescription()));
            }
        }
    }

    /** Chamadas que as páginas podem fazer (só ações inofensivas). */
    private class Ponte {
        @JavascriptInterface
        public void tentarDeNovo() {
            principal.post(MainActivity.this::carregar);
        }

        @JavascriptInterface
        public void trocarServidor() {
            principal.post(MainActivity.this::pedirServidor);
        }
    }
}
