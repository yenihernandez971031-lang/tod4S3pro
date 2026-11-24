package com.tod4s3.ai;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.LinearGradient;
import android.graphics.Shader;
import android.graphics.drawable.GradientDrawable;
import android.animation.ObjectAnimator;
import android.animation.PropertyValuesHolder;
import android.os.Bundle;
import android.os.SystemClock;
import android.provider.Settings;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.RecognitionListener;
import android.speech.tts.TextToSpeech;
import android.app.Dialog;
import android.view.Window;
import android.view.WindowManager;
import android.text.InputType;
import android.text.InputFilter;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Independent AiTod4S3 client. It never uses Tod4S3 Sync pairing or Premium. */
public final class AiActivity extends Activity {
    private static final String API = "https://tod4s3pro-production.up.railway.app";
    private static final int BG = Color.rgb(12, 13, 19);
    private static final int PANEL = Color.rgb(25, 27, 36);
    private static final int RED = Color.rgb(255, 59, 87);
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ArrayList<JSONObject> history = new ArrayList<>();
    private SharedPreferences prefs;
    private String token = "";
    private String username = "";
    private LinearLayout root;
    private LinearLayout transcript;
    private LinearLayout welcomePanel;
    private ScrollView scroll;
    private EditText userField;
    private EditText passwordField;
    private EditText composer;
    private TextView status;
    private TextView chatStatus;
    private Button loginButton;
    private ImageButton sendButton;
    private ImageButton callButton;
    private Switch speakSwitch;
    private SpeechRecognizer speechRecognizer;
    private Dialog voiceCallDialog;
    private FrameLayout voiceCallOrb;
    private TextView voiceCallStatus;
    private boolean voiceCallActive = false;
    private boolean voiceCallSpeaking = false;
    private long speechStartedAt;
    private boolean sending = false;
    private boolean ttsReady = false;
    private boolean micPermissionPending = false;
    private TextToSpeech tts;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(9, 10, 15));
        getWindow().setNavigationBarColor(Color.rgb(9, 10, 15));
        prefs = getSharedPreferences("ai_tod4s3_private", MODE_PRIVATE);
        token = prefs.getString("session_token", "");
        username = prefs.getString("session_user", "");
        tts = new TextToSpeech(this, result -> {
            if (result == TextToSpeech.SUCCESS) {
                int lang = tts.setLanguage(new Locale("es", "ES"));
                ttsReady = lang != TextToSpeech.LANG_MISSING_DATA && lang != TextToSpeech.LANG_NOT_SUPPORTED;
            }
        });
        if (!token.isEmpty() && !username.isEmpty()) {
            loadHistory();
            showChat();
        } else showLogin();
    }

    private void showLogin() {
        root = base();
        FrameLayout stage = new FrameLayout(this);
        stage.addView(new ConstellationView(this), new FrameLayout.LayoutParams(-1, -1));
        LinearLayout content = column();
        content.setGravity(Gravity.CENTER_HORIZONTAL);
        content.setPadding(dp(14), dp(22), dp(14), dp(18));
        LinearLayout online = new LinearLayout(this);
        online.setGravity(Gravity.CENTER); online.setPadding(dp(10), dp(5), dp(10), dp(5));
        online.setBackground(roundStroke(Color.argb(35, 82, 235, 151), 18, Color.argb(140, 82, 235, 151)));
        TextView onlineDot = label("●", 9, true, Color.rgb(82, 235, 151));
        onlineDot.setGravity(Gravity.CENTER); online.addView(onlineDot, lp(14, 18));
        TextView onlineText = label("IA en línea", 11, true, Color.rgb(82, 235, 151));
        onlineText.setGravity(Gravity.CENTER); online.addView(onlineText, lp(-2, 18));
        content.addView(online, lp(-2, -2));
        ObjectAnimator blink = ObjectAnimator.ofFloat(onlineDot, View.ALPHA, 1f, .28f, 1f);
        blink.setDuration(1200); blink.setRepeatCount(ObjectAnimator.INFINITE); blink.start();
        FrameLayout orb = createAiOrb(dp(142));
        content.addView(orb, lp(142, 142));
        TextView title = label("AiTod4S3", 31, true, Color.WHITE);
        title.setGravity(Gravity.CENTER);
        title.setLetterSpacing(.08f);
        content.addView(title, margins(-1, -2, 0, 12, 0, 0));
        TextView subtitle = label("Tu asistente inteligente", 15, false, Color.rgb(167, 172, 188));
        subtitle.setGravity(Gravity.CENTER);
        content.addView(subtitle, margins(-1, -2, 0, 0, 0, 22));
        int fieldWidth = Math.min(dp(344), getResources().getDisplayMetrics().widthPixels - dp(56));
        FrameLayout userBox = crystalField("◯", "Usuario", false);
        userField = (EditText) userBox.getTag();
        LinearLayout.LayoutParams fieldParams = new LinearLayout.LayoutParams(fieldWidth, dp(62));
        fieldParams.gravity = Gravity.CENTER_HORIZONTAL;
        content.addView(userBox, fieldParams);
        space(content, 12);
        FrameLayout passwordBox = crystalField("▢", "Contraseña", true);
        passwordField = (EditText) passwordBox.getTag();
        passwordField.setImeOptions(EditorInfo.IME_ACTION_DONE);
        LinearLayout.LayoutParams passwordParams = new LinearLayout.LayoutParams(fieldWidth, dp(62));
        passwordParams.gravity = Gravity.CENTER_HORIZONTAL;
        content.addView(passwordBox, passwordParams);
        space(content, 15);
        loginButton = button("INICIAR SESIÓN  →", RED);
        loginButton.setOnClickListener(v -> login());
        LinearLayout.LayoutParams loginParams = new LinearLayout.LayoutParams(fieldWidth, dp(56));
        loginParams.gravity = Gravity.CENTER_HORIZONTAL;
        content.addView(loginButton, loginParams);
        status = label("▢  Acceso con la cuenta AiTod4S3 que te creó el administrador.", 12, false, Color.rgb(181, 188, 201));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(12), dp(12), dp(12), dp(12));
        status.setBackground(roundStroke(Color.argb(38, 255, 255, 255), 16, Color.argb(45, 255, 255, 255)));
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(fieldWidth, -2);
        statusParams.setMargins(0, dp(14), 0, 0);
        statusParams.gravity = Gravity.CENTER_HORIZONTAL;
        content.addView(status, statusParams);
        TextView footer = label("Conexión cifrada  ·  ✦", 11, false, Color.rgb(121, 128, 147));
        footer.setGravity(Gravity.CENTER);
        content.addView(footer, lp(-1, -2));
        FrameLayout.LayoutParams contentParams = new FrameLayout.LayoutParams(
                Math.min(dp(390), getResources().getDisplayMetrics().widthPixels - dp(28)), -1, Gravity.CENTER);
        stage.addView(content, contentParams);
        root.addView(stage, new LinearLayout.LayoutParams(-1, 0, 1f));
        ObjectAnimator breathe = ObjectAnimator.ofPropertyValuesHolder(orb,
                PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.035f, 1f),
                PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.035f, 1f));
        breathe.setDuration(1800); breathe.setRepeatCount(ObjectAnimator.INFINITE); breathe.start();
    }

    private void login() {
        final String name = userField.getText().toString().trim();
        final String pass = passwordField.getText().toString();
        if (name.isEmpty() || pass.isEmpty()) { status.setText("Escribe tu usuario y contraseña."); return; }
        loginButton.setEnabled(false);
        status.setText("Verificando acceso…");
        io.execute(() -> {
            try {
                JSONObject request = new JSONObject().put("username", name).put("password", pass).put("device_id", deviceId());
                JSONObject response = post("/api/ai/login", null, request);
                String received = response.optString("token", "");
                if (received.isEmpty()) throw new Exception("No se recibió una sesión válida.");
                runOnUiThread(() -> {
                    token = received;
                    username = name;
                    prefs.edit().putString("session_token", token).putString("session_user", username).apply();
                    history.clear();
                    loadHistory();
                    showChat();
                });
            } catch (Exception e) {
                String message = friendlyError(e);
                runOnUiThread(() -> {
                    loginButton.setEnabled(true);
                    status.setText(message);
                });
            }
        });
    }

    private void showChat() {
        root = base();
        welcomePanel = null;
        LinearLayout page = column();
        page.setPadding(dp(14), dp(12), dp(14), dp(10));
        LinearLayout header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        FrameLayout orb = createAiOrb(dp(34));
        LinearLayout.LayoutParams orbParams = lp(34, 34);
        orbParams.setMargins(0, 0, dp(10), 0);
        header.addView(orb, orbParams);
        LinearLayout identity = column();
        identity.addView(label("AiTod4S3", 19, true, Color.WHITE), lp(-1, -2));
        chatStatus = label("en línea", 12, true, Color.rgb(53, 224, 140));
        identity.addView(chatStatus, lp(-1, -2));
        header.addView(identity, new LinearLayout.LayoutParams(0, -2, 1f));
        callButton = iconButton(R.drawable.ic_mic, Color.WHITE, "Iniciar llamada de voz con AiTod4S3");
        callButton.setBackground(roundStroke(Color.rgb(29, 31, 40), 15, Color.rgb(78, 82, 98)));
        callButton.setOnClickListener(v -> toggleVoiceCall());
        header.addView(callButton, lp(42, 42));
        Button logout = button("SALIR", PANEL);
        logout.setTextSize(11);
        logout.setBackground(roundStroke(PANEL, 14, Color.rgb(78, 82, 98)));
        logout.setOnClickListener(v -> logout());
        header.addView(logout, lp(74, 40));
        page.addView(header, lp(-1, 48));
        View line = new View(this);
        line.setBackgroundColor(Color.rgb(52, 55, 68));
        page.addView(line, lp(-1, 1));
        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setVerticalScrollBarEnabled(false);
        transcript = column();
        transcript.setPadding(dp(2), dp(12), dp(2), dp(14));
        scroll.addView(transcript, new ScrollView.LayoutParams(-1, -2));
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
        if (history.isEmpty()) addWelcomeState();
        else for (JSONObject item : history) addBubble(item.optString("content", ""), "user".equals(item.optString("role")));
        LinearLayout voiceRow = new LinearLayout(this);
        voiceRow.setGravity(Gravity.CENTER_VERTICAL);
        speakSwitch = new Switch(this);
        speakSwitch.setText("La IA me habla");
        speakSwitch.setTextColor(Color.rgb(190, 194, 207));
        speakSwitch.setTextSize(13);
        speakSwitch.setChecked(prefs.getBoolean("speak_enabled", false));
        speakSwitch.setOnCheckedChangeListener((view, checked) -> {
            prefs.edit().putBoolean("speak_enabled", checked).apply();
            if (!checked && tts != null) tts.stop();
        });
        voiceRow.addView(speakSwitch, new LinearLayout.LayoutParams(0, dp(42), 1f));
        TextView microphoneHint = label("Micrófono para dictar", 11, false, Color.rgb(134, 141, 160));
        voiceRow.addView(microphoneHint, lp(-2, 42));
        page.addView(voiceRow, lp(-1, 42));
        FrameLayout composerShell = new FrameLayout(this);
        composerShell.setPadding(dp(7), dp(6), dp(7), dp(6));
        composerShell.setBackground(roundStroke(Color.rgb(11, 12, 18), 30, RED));
        LinearLayout composerRow = new LinearLayout(this);
        composerRow.setGravity(Gravity.CENTER_VERTICAL);
        ImageView sparkle = new ImageView(this);
        sparkle.setImageResource(R.drawable.ic_ai_sparkle);
        sparkle.setColorFilter(RED);
        composerRow.addView(sparkle, lp(22, 22));
        composer = new EditText(this);
        composer.setSingleLine(true);
        composer.setHint("Pregúntale a AiTod4S3…");
        composer.setTextColor(Color.rgb(244, 245, 249));
        composer.setHintTextColor(Color.rgb(109, 115, 132));
        composer.setTextSize(15);
        composer.setPadding(dp(7), 0, dp(5), 0);
        composer.setBackgroundColor(Color.TRANSPARENT);
        composer.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        composer.setFilters(new InputFilter[]{new InputFilter.LengthFilter(2500)});
        composer.setImeOptions(EditorInfo.IME_ACTION_SEND);
        composerRow.addView(composer, new LinearLayout.LayoutParams(0, dp(44), 1f));
        ImageButton mic = iconButton(R.drawable.ic_mic, Color.WHITE, "Dictar mensaje por voz");
        mic.setBackground(roundStroke(Color.rgb(31, 33, 42), 22, Color.argb(52, 255, 255, 255)));
        mic.setOnClickListener(v -> beginDictation());
        composerRow.addView(mic, lp(42, 42));
        sendButton = iconButton(R.drawable.ic_send, Color.WHITE, "Enviar mensaje a AiTod4S3");
        sendButton.setBackground(gradientRound(new int[]{RED, Color.rgb(193, 18, 46)}, 24));
        sendButton.setOnClickListener(v -> sendMessage());
        composerRow.addView(sendButton, lp(44, 44));
        composerShell.addView(composerRow, new FrameLayout.LayoutParams(-1, -1));
        page.addView(composerShell, lp(-1, -2));
        composer.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND) { sendMessage(); return true; }
            return false;
        });
        root.addView(page, new LinearLayout.LayoutParams(-1, 0, 1f));
    }

    private void sendMessage() {
        if (sending || composer == null) return;
        String text = composer.getText().toString().trim();
        if (text.isEmpty()) return;
        composer.setText("");
        if (welcomePanel != null && welcomePanel.getParent() == transcript) {
            transcript.removeView(welcomePanel);
            welcomePanel = null;
        }
        JSONObject userMessage = new JSONObject();
        try { userMessage.put("role", "user").put("content", text); } catch (JSONException ignored) { return; }
        history.add(userMessage);
        trimHistory();
        saveHistory();
        addBubble(text, true);
        addThinkingBubble();
        sending = true;
        sendButton.setEnabled(false);
        if (chatStatus != null) chatStatus.setText("pensando…");
        JSONArray messages = new JSONArray();
        int from = Math.max(0, history.size() - 12);
        for (int i = from; i < history.size(); i++) messages.put(history.get(i));
        io.execute(() -> {
            try {
                JSONObject response = post("/api/ai/chat", token, new JSONObject().put("messages", messages));
                String reply = response.optString("reply", "");
                if (reply.isEmpty()) throw new Exception("La IA devolvió una respuesta vacía.");
                runOnUiThread(() -> {
                    removeThinkingBubble();
                    try {
                        JSONObject assistant = new JSONObject().put("role", "assistant").put("content", reply);
                        history.add(assistant);
                        trimHistory();
                        saveHistory();
                    } catch (JSONException ignored) { }
                    addBubble(reply, false);
                    if (((speakSwitch != null && speakSwitch.isChecked()) || voiceCallActive) && ttsReady && tts != null) {
                        voiceCallSpeaking = voiceCallActive;
                        tts.speak(reply, TextToSpeech.QUEUE_FLUSH, null, "aitod4s3-reply");
                        if (voiceCallActive) postDelayedVoiceListening(reply);
                    }
                    sending = false;
                    sendButton.setEnabled(true);
                    if (chatStatus != null) chatStatus.setText("en línea");
                });
            } catch (Exception e) {
                String error = friendlyError(e);
                boolean unauthorized = e.getMessage() != null && e.getMessage().contains("unauthorized");
                runOnUiThread(() -> {
                    removeThinkingBubble();
                    Toast.makeText(this, error, Toast.LENGTH_LONG).show();
                    sending = false;
                    sendButton.setEnabled(true);
                    if (chatStatus != null) chatStatus.setText("en línea");
                    if (unauthorized) logout();
                });
            }
        });
    }

    private void beginDictation() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micPermissionPending = true;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 18);
            return;
        }
        launchRecognizer();
    }

    private void launchRecognizer() {
        try {
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "es-CU");
            intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "Habla con AiTod4S3");
            startActivityForResult(intent, 17);
        } catch (Exception e) {
            Toast.makeText(this, "No hay un servicio de reconocimiento de voz disponible.", Toast.LENGTH_LONG).show();
        }
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == 18 && micPermissionPending) {
            micPermissionPending = false;
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) launchRecognizer();
            else Toast.makeText(this, "Permite el micrófono para dictar; también puedes escribir.", Toast.LENGTH_LONG).show();
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 17 && result == RESULT_OK && data != null) {
            ArrayList<String> results = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if (results != null && !results.isEmpty() && composer != null) {
                composer.setText(results.get(0));
                composer.setSelection(composer.length());
            }
        }
    }

    private JSONObject post(String path, String bearer, JSONObject payload) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(API + path).openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(20000);
        connection.setReadTimeout(path.endsWith("/chat") ? 90000 : 30000);
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        connection.setRequestProperty("Accept", "application/json");
        if (bearer != null && !bearer.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
            connection.setRequestProperty("X-Ai-Device-Id", deviceId());
        }
        try {
            byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream output = connection.getOutputStream()) { output.write(bytes); }
            int code = connection.getResponseCode();
            InputStream stream = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
            StringBuilder text = new StringBuilder();
            if (stream != null) {
                try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = reader.readLine()) != null && text.length() < 1_000_000) text.append(line);
                }
            }
            JSONObject response = new JSONObject(text.toString());
            if (code < 200 || code >= 300 || !response.optBoolean("ok", false))
                throw new Exception(response.optString("error", "http_" + code));
            return response;
        } finally { connection.disconnect(); }
    }

    private void logout() {
        String old = token;
        token = "";
        username = "";
        prefs.edit().remove("session_token").remove("session_user").apply();
        if (!old.isEmpty()) io.execute(() -> {
            try { post("/api/ai/logout", old, new JSONObject()); } catch (Exception ignored) { }
        });
        if (tts != null) tts.stop();
        history.clear();
        showLogin();
    }

    private String deviceId() {
        String value = Settings.Secure.getString(getContentResolver(), Settings.Secure.ANDROID_ID);
        return value == null ? "" : value.toLowerCase(Locale.US);
    }

    private String historyKey() { return "conversation_" + username; }
    private void loadHistory() {
        history.clear();
        try {
            JSONArray saved = new JSONArray(prefs.getString(historyKey(), "[]"));
            for (int i = Math.max(0, saved.length() - 40); i < saved.length(); i++) {
                JSONObject row = saved.optJSONObject(i);
                if (row != null && ("user".equals(row.optString("role")) || "assistant".equals(row.optString("role"))))
                    history.add(new JSONObject().put("role", row.optString("role")).put("content", row.optString("content")));
            }
        } catch (Exception ignored) { history.clear(); }
    }
    private void saveHistory() {
        JSONArray saved = new JSONArray();
        for (JSONObject row : history) saved.put(row);
        prefs.edit().putString(historyKey(), saved.toString()).apply();
    }
    private void trimHistory() { while (history.size() > 40) history.remove(0); }

    private void addWelcomeState() {
        welcomePanel = column();
        welcomePanel.setGravity(Gravity.CENTER);
        welcomePanel.setPadding(dp(8), dp(20), dp(8), dp(20));
        welcomePanel.setMinimumHeight(Math.max(dp(430), getResources().getDisplayMetrics().heightPixels - dp(350)));

        FrameLayout orb = createAiOrb(dp(96));
        welcomePanel.addView(orb, lp(96, 96));
        TextView title = label("Hola, soy AiTod4S3", 26, true, Color.WHITE);
        title.setGravity(Gravity.CENTER);
        welcomePanel.addView(title, margins(-1, -2, 0, 16, 0, 4));
        TextView subtitle = label("Tu asistente inteligente, listo para ayudarte", 14, false, Color.rgb(170, 176, 194));
        subtitle.setGravity(Gravity.CENTER);
        welcomePanel.addView(subtitle, margins(-1, -2, 0, 0, 0, 20));

        String[] titles = {"Explícame algo", "Ayúdame a escribir", "Resume un tema", "Dame ideas"};
        String[] hints = {"Responde con claridad", "Un mensaje o un texto", "Lo esencial, paso a paso", "Empecemos una lluvia de ideas"};
        String[] prompts = {
                "Explícame de forma sencilla un tema que te indique.",
                "Ayúdame a redactar un mensaje claro y natural.",
                "Ayúdame a resumir un tema en sus ideas principales.",
                "Dame ideas creativas para un proyecto."
        };
        LinearLayout grid = column();
        for (int rowIndex = 0; rowIndex < 2; rowIndex++) {
            LinearLayout row = new LinearLayout(this);
            for (int col = 0; col < 2; col++) {
                int index = rowIndex * 2 + col;
                LinearLayout card = column();
                card.setGravity(Gravity.CENTER_VERTICAL);
                card.setPadding(dp(13), dp(11), dp(12), dp(11));
                card.setBackground(roundStroke(Color.rgb(15, 16, 23), 19,
                        index == 0 ? Color.rgb(255, 59, 87) : Color.argb(38, 255, 255, 255)));
                ImageView icon = new ImageView(this);
                icon.setImageResource(R.drawable.ic_ai_sparkle);
                icon.setColorFilter(index == 0 ? Color.WHITE : RED);
                icon.setPadding(dp(7), dp(7), dp(7), dp(7));
                icon.setBackground(gradientRound(new int[]{RED, Color.rgb(193, 18, 46)}, 11));
                card.addView(icon, lp(32, 32));
                card.addView(label(titles[index], 14, true, Color.WHITE), margins(-1, -2, 0, 8, 0, 0));
                TextView hint = label(hints[index], 11, false, Color.rgb(160, 166, 182));
                card.addView(hint, margins(-1, -2, 0, 3, 0, 0));
                card.setOnClickListener(v -> sendSuggestedPrompt(prompts[index]));
                LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(0, dp(106), 1f);
                if (col > 0) cardParams.setMargins(dp(8), 0, 0, 0);
                row.addView(card, cardParams);
            }
            LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(-1, dp(106));
            if (rowIndex > 0) rowParams.setMargins(0, dp(8), 0, 0);
            grid.addView(row, rowParams);
        }
        welcomePanel.addView(grid, lp(-1, -2));
        TextView disclaimer = label("Puede equivocarse. Verifica la información importante.", 11, false, Color.rgb(133, 139, 156));
        disclaimer.setGravity(Gravity.CENTER);
        welcomePanel.addView(disclaimer, margins(-1, -2, 0, 15, 0, 0));
        transcript.addView(welcomePanel, new LinearLayout.LayoutParams(-1, -2));
    }

    private void sendSuggestedPrompt(String prompt) {
        if (composer == null) return;
        composer.setText(prompt);
        sendMessage();
    }

    private FrameLayout createAiOrb(int size) {
        FrameLayout orb = new FrameLayout(this);
        GradientDrawable background = new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[]{RED, Color.rgb(229, 154, 20)});
        background.setShape(GradientDrawable.OVAL);
        background.setStroke(dp(1), Color.argb(70, 255, 255, 255));
        orb.setBackground(background);
        orb.setElevation(dp(4));
        FrameLayout ring = new FrameLayout(this);
        ring.setBackground(roundStroke(Color.TRANSPARENT, 100, Color.argb(115, 255, 196, 62)));
        FrameLayout.LayoutParams ringParams = new FrameLayout.LayoutParams(Math.max(dp(20), size - dp(12)), Math.max(dp(20), size - dp(12)), Gravity.CENTER);
        orb.addView(ring, ringParams);
        FrameLayout ring2 = new FrameLayout(this);
        ring2.setBackground(roundStroke(Color.TRANSPARENT, 100, Color.argb(75, 255, 115, 72)));
        FrameLayout.LayoutParams ring2Params = new FrameLayout.LayoutParams(Math.max(dp(18), size - dp(28)), Math.max(dp(18), size - dp(28)), Gravity.CENTER);
        orb.addView(ring2, ring2Params);
        ImageView sparkle = new ImageView(this);
        sparkle.setImageResource(R.drawable.ic_ai_sparkle);
        sparkle.setColorFilter(Color.argb(230, 255, 255, 255));
        int glyphSize = Math.max(dp(17), size / 2);
        orb.addView(sparkle, new FrameLayout.LayoutParams(glyphSize, glyphSize, Gravity.CENTER));
        return orb;
    }

    private void addBubble(String text, boolean mine) {
        if (mine) {
            TextView bubble = label(text, 15, true, Color.WHITE);
            bubble.setGravity(Gravity.CENTER_VERTICAL);
            bubble.setPadding(dp(16), dp(10), dp(16), dp(10));
            bubble.setBackground(gradientRound(new int[]{RED, Color.rgb(193, 18, 46)}, 18));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams((int) (getResources().getDisplayMetrics().widthPixels * .82f), -2);
            params.gravity = Gravity.RIGHT;
            params.setMargins(dp(4), dp(4), dp(4), dp(10));
            transcript.addView(bubble, params);
        } else {
            LinearLayout answer = column();
            LinearLayout author = new LinearLayout(this);
            author.setGravity(Gravity.CENTER_VERTICAL);
            author.addView(createAiOrb(dp(26)), lp(26, 26));
            author.addView(label("AiTod4S3", 15, true, Color.WHITE), margins(-2, -2, 8, 0, 0, 0));
            answer.addView(author, lp(-1, -2));
            TextView body = label(text, 15, false, Color.rgb(233, 235, 243));
            body.setLineSpacing(dp(5), 1f);
            answer.addView(body, margins(-1, -2, 0, 8, 0, 0));
            transcript.addView(answer, margins(-1, -2, 3, 5, 3, 13));
        }
        scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
    }

    private void addThinkingBubble() {
        TextView bubble = label("AiTod4S3 está pensando…", 14, true, Color.rgb(234, 249, 255));
        bubble.setPadding(dp(15), dp(13), dp(15), dp(13));
        bubble.setBackground(roundStroke(Color.rgb(15, 17, 23), 20, Color.argb(90, 56, 225, 255)));
        transcript.addView(bubble, margins(-1, -2, 4, 2, 4, 8));
        scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
    }
    private void removeThinkingBubble() {
        if (transcript != null && transcript.getChildCount() > 0) {
            View last = transcript.getChildAt(transcript.getChildCount() - 1);
            if (last instanceof TextView && ((TextView) last).getText().toString().startsWith("AiTod4S3 está pensando"))
                transcript.removeView(last);
        }
    }

    private void toggleVoiceCall() {
        if (voiceCallActive) { stopVoiceCall(); return; }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 18); return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            Toast.makeText(this, "El dictado de voz no está disponible en este teléfono.", Toast.LENGTH_LONG).show(); return;
        }
        voiceCallActive = true;
        if (callButton != null) callButton.setBackground(gradientRound(new int[]{RED, Color.rgb(193, 18, 46)}, 15));
        showVoiceCallDialog(); beginVoiceRecognition();
    }

    private void beginVoiceRecognition() {
        if (!voiceCallActive) return;
        if (speechRecognizer == null) speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
        speechRecognizer.cancel();
        speechRecognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) { setVoiceCallStatus("ESCUCHANDO…"); }
            @Override public void onBeginningOfSpeech() { speechStartedAt = SystemClock.uptimeMillis(); }
            @Override public void onRmsChanged(float rms) { }
            @Override public void onBufferReceived(byte[] buffer) { }
            @Override public void onEndOfSpeech() { setVoiceCallStatus("PROCESANDO…"); }
            @Override public void onError(int error) { if (voiceCallActive && !sending) postDelayed(() -> beginVoiceRecognition(), 180); }
            @Override public void onResults(Bundle results) {
                ArrayList<String> values = results == null ? null : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                String phrase = values == null || values.isEmpty() ? "" : values.get(0).trim();
                long duration = speechStartedAt == 0 ? 1000 : SystemClock.uptimeMillis() - speechStartedAt;
                if (phrase.length() >= 2 && (duration >= 180 || phrase.length() >= 5) && composer != null && voiceCallActive) {
                    composer.setText(phrase); composer.setSelection(composer.length()); setVoiceCallStatus("CONECTANDO CON LA IA…"); sendMessage();
                } else if (voiceCallActive) postDelayed(() -> beginVoiceRecognition(), 150);
            }
            @Override public void onPartialResults(Bundle partialResults) { }
            @Override public void onEvent(int eventType, Bundle params) { }
        });
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "es-CU");
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "Habla con AiTod4S3");
        speechRecognizer.startListening(intent);
    }

    private void postDelayedVoiceListening(String reply) {
        long delay = Math.max(1200L, Math.min(9000L, reply.length() * 48L));
        postDelayed(() -> { voiceCallSpeaking = false; if (voiceCallActive && !sending) beginVoiceRecognition(); }, delay);
    }

    private void showVoiceCallDialog() {
        LinearLayout panel = column(); panel.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.setPadding(dp(26), dp(34), dp(26), dp(24)); panel.setBackgroundColor(Color.rgb(7, 8, 12));
        TextView eyebrow = label("LLAMADA DE VOZ", 11, true, Color.rgb(255, 122, 137)); eyebrow.setGravity(Gravity.CENTER);
        panel.addView(eyebrow, lp(-1, -2));
        TextView heading = label("AiTod4S3", 29, true, Color.WHITE); heading.setGravity(Gravity.CENTER);
        panel.addView(heading, margins(-1, -2, 0, 8, 0, 3));
        TextView hint = label("Habla normalmente. La IA te responderá en voz alta.", 13, false, Color.rgb(170, 176, 194)); hint.setGravity(Gravity.CENTER);
        panel.addView(hint, margins(-1, -2, 0, 0, 0, 28));
        voiceCallOrb = createAiOrb(dp(150)); panel.addView(voiceCallOrb, lp(150, 150));
        voiceCallStatus = label("CONECTANDO…", 14, true, Color.rgb(96, 226, 244)); voiceCallStatus.setGravity(Gravity.CENTER);
        panel.addView(voiceCallStatus, margins(-1, -2, 0, 24, 0, 7));
        TextView flow = label("Escuchando → procesando → respondiendo", 12, false, Color.rgb(138, 144, 166)); flow.setGravity(Gravity.CENTER);
        panel.addView(flow, margins(-1, -2, 0, 0, 0, 28));
        Button hangup = button("FINALIZAR LLAMADA", RED); hangup.setOnClickListener(v -> stopVoiceCall()); panel.addView(hangup, lp(-1, 54));
        voiceCallDialog = new Dialog(this); voiceCallDialog.setContentView(panel); voiceCallDialog.setCancelable(false); voiceCallDialog.show();
        Window window = voiceCallDialog.getWindow();
        if (window != null) { window.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.rgb(7, 8, 12))); window.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT); }
        ObjectAnimator pulse = ObjectAnimator.ofPropertyValuesHolder(voiceCallOrb, PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.08f, 1f), PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.08f, 1f));
        pulse.setDuration(900); pulse.setRepeatCount(ObjectAnimator.INFINITE); pulse.start();
    }

    private void setVoiceCallStatus(String value) { if (voiceCallStatus != null) voiceCallStatus.setText(value); }
    private void stopVoiceCall() {
        voiceCallActive = false; voiceCallSpeaking = false;
        if (speechRecognizer != null) speechRecognizer.cancel(); if (tts != null) tts.stop();
        if (voiceCallDialog != null) { voiceCallDialog.dismiss(); voiceCallDialog = null; }
        voiceCallOrb = null; voiceCallStatus = null;
        if (callButton != null) callButton.setBackground(roundStroke(Color.rgb(29, 31, 40), 15, Color.rgb(78, 82, 98)));
    }

    private void postDelayed(Runnable action, long delay) { getWindow().getDecorView().postDelayed(action, delay); }

    private String friendlyError(Exception e) {
        String raw = e.getMessage() == null ? "" : e.getMessage();
        if (raw.contains("invalid_credentials")) return "Usuario o contraseña incorrectos.";
        if (raw.contains("device_in_use")) return "Esta cuenta ya está vinculada a otro dispositivo. Pide al administrador que libere el dispositivo anterior.";
        if (raw.contains("invalid_device")) return "No se pudo identificar este dispositivo Android.";
        if (raw.contains("unauthorized")) return "La sesión venció. Inicia sesión otra vez.";
        if (raw.contains("login_rate_limited")) return "Demasiados intentos. Espera unos minutos.";
        if (raw.contains("ai_rate_limited")) return "Espera un momento antes de enviar otro mensaje.";
        if (raw.contains("ai_not_configured") || raw.contains("ai_unavailable")) return "La IA no está disponible temporalmente.";
        if (raw.contains("UnknownHostException") || raw.contains("ConnectException")) return "No hay conexión con AiTod4S3. Comprueba Internet.";
        return "No se pudo completar la solicitud. Comprueba la conexión e inténtalo de nuevo.";
    }

    private final class ConstellationView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float[][] points = {{.08f,.16f},{.22f,.09f},{.39f,.19f},{.62f,.11f},{.84f,.18f},{.14f,.39f},{.31f,.31f},{.52f,.38f},{.76f,.34f},{.91f,.48f},{.07f,.68f},{.27f,.58f},{.47f,.69f},{.69f,.59f},{.88f,.76f}};
        ConstellationView(android.content.Context context) { super(context); paint.setStrokeWidth(dp(1)); }
        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas); canvas.drawColor(Color.rgb(7, 9, 17));
            float w=getWidth(), h=getHeight();
            paint.setShader(new android.graphics.RadialGradient(w * .5f, h * .30f, Math.max(w, h) * .42f,
                    Color.argb(105, 104, 18, 28), Color.TRANSPARENT, Shader.TileMode.CLAMP));
            canvas.drawCircle(w * .5f, h * .30f, Math.max(w, h) * .42f, paint);
            paint.setShader(null);
            paint.setStyle(Paint.Style.STROKE); paint.setColor(Color.argb(34,255,102,82));
            for (int i=0;i<points.length-1;i++) canvas.drawLine(points[i][0]*w,points[i][1]*h,points[i+1][0]*w,points[i+1][1]*h,paint);
            paint.setStyle(Paint.Style.FILL);
            for (int i=0;i<points.length;i++) { int c=i%3==0?Color.rgb(255,92,78):i%3==1?Color.rgb(255,196,62):Color.WHITE; paint.setColor(Color.argb(150,c>>16&255,c>>8&255,c&255)); canvas.drawCircle(points[i][0]*w,points[i][1]*h,dp(i%4==0?2:1),paint); }
            postInvalidateDelayed(1200);
        }
    }

    private LinearLayout base() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setBackgroundColor(BG);
        setContentView(view);
        return view;
    }
    private LinearLayout column() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); return l; }
    private TextView label(String text, int size, boolean bold, int color) {
        TextView v = new TextView(this); v.setText(text); v.setTextSize(size); v.setTextColor(color);
        if (bold) v.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return v;
    }
    private FrameLayout crystalField(String iconText, String fieldLabel, boolean password) {
        FrameLayout box = new FrameLayout(this);
        box.setPadding(dp(10), dp(4), dp(password ? 48 : 10), dp(2));
        box.setBackground(roundStroke(Color.argb(52, 28, 29, 38), 19, Color.argb(75, 255, 255, 255)));
        TextView icon = label(iconText, 18, true, password ? Color.rgb(148, 153, 170) : Color.rgb(255, 77, 103));
        icon.setGravity(Gravity.CENTER);
        icon.setBackground(roundStroke(Color.argb(35, 255, 255, 255), 30, Color.argb(75, 255, 255, 255)));
        box.addView(icon, new FrameLayout.LayoutParams(dp(36), dp(36), Gravity.CENTER_VERTICAL | Gravity.START));
        TextView caption = label(fieldLabel, 10, true, password ? Color.rgb(148, 153, 170) : Color.rgb(255, 77, 103));
        FrameLayout.LayoutParams captionParams = new FrameLayout.LayoutParams(-1, dp(18));
        captionParams.leftMargin = dp(48); captionParams.topMargin = dp(4);
        box.addView(caption, captionParams);
        EditText value = edit("", password ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        value.setHint(""); value.setSingleLine(true); value.setTextSize(15); value.setPadding(0, 0, 0, 0); value.setBackgroundColor(Color.TRANSPARENT);
        FrameLayout.LayoutParams valueParams = new FrameLayout.LayoutParams(-1, dp(28));
        valueParams.leftMargin = dp(48); valueParams.topMargin = dp(25);
        box.addView(value, valueParams);
        if (password) {
            TextView eye = label("◉", 17, false, Color.rgb(160, 166, 183)); eye.setGravity(Gravity.CENTER); eye.setContentDescription("Ver u ocultar contraseña");
            eye.setOnClickListener(v -> { boolean visible = (value.getInputType() & InputType.TYPE_TEXT_VARIATION_PASSWORD) == 0; value.setInputType(InputType.TYPE_CLASS_TEXT | (visible ? InputType.TYPE_TEXT_VARIATION_PASSWORD : InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD)); value.setSelection(value.length()); eye.setText(visible ? "◉" : "⊘"); });
            box.addView(eye, new FrameLayout.LayoutParams(dp(38), dp(38), Gravity.END | Gravity.CENTER_VERTICAL));
        }
        box.setTag(value);
        value.setOnFocusChangeListener((v, focused) -> box.setBackground(roundStroke(Color.argb(60, 36, 30, 42), 19, focused ? Color.rgb(255, 98, 82) : Color.argb(75, 255, 255, 255))));
        return box;
    }
    private EditText edit(String hint, int inputType) {
        EditText v = new EditText(this); v.setSingleLine(false); v.setHint(hint); v.setTextColor(Color.WHITE);
        v.setHintTextColor(Color.rgb(120, 126, 145)); v.setTextSize(15); v.setInputType(inputType);
        v.setPadding(dp(14), dp(8), dp(14), dp(8)); v.setBackground(round(PANEL, 14));
        return v;
    }
    private Button button(String text, int color) {
        Button v = new Button(this); v.setText(text); v.setTextColor(Color.WHITE); v.setTextSize(14);
        v.setTypeface(Typeface.DEFAULT, Typeface.BOLD); v.setBackground(round(color, 14));
        return v;
    }
    private ImageButton iconButton(int iconRes, int color, String description) {
        ImageButton v = new ImageButton(this);
        v.setImageResource(iconRes);
        v.setColorFilter(color);
        v.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
        v.setContentDescription(description);
        v.setPadding(0, 0, 0, 0);
        v.setBackground(round(PANEL, 22));
        return v;
    }
    private GradientDrawable round(int color, int radius) {
        GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius)); return d;
    }
    private GradientDrawable roundStroke(int color, int radius, int strokeColor) {
        GradientDrawable d = round(color, radius);
        d.setStroke(dp(1), strokeColor);
        return d;
    }
    private GradientDrawable gradientRound(int[] colors, int radius) {
        GradientDrawable d = new GradientDrawable(GradientDrawable.Orientation.TL_BR, colors);
        d.setCornerRadius(dp(radius));
        return d;
    }
    private LinearLayout.LayoutParams lp(int width, int height) { return new LinearLayout.LayoutParams(width < 0 ? width : dp(width), height < 0 ? height : dp(height)); }
    private LinearLayout.LayoutParams margins(int width, int height, int left, int top, int right, int bottom) {
        LinearLayout.LayoutParams params = lp(width, height);
        params.setMargins(dp(left), dp(top), dp(right), dp(bottom));
        return params;
    }
    private void space(LinearLayout parent, int height) { parent.addView(new View(this), lp(1, height)); }
    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + .5f); }

    @Override protected void onDestroy() {
        if (tts != null) { tts.stop(); tts.shutdown(); }
        io.shutdownNow();
        super.onDestroy();
    }

}
