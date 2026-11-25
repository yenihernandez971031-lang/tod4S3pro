package com.tod4s3.ai;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.net.Uri;
import android.database.Cursor;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.LinearGradient;
import android.graphics.Shader;
import android.graphics.BitmapFactory;
import android.graphics.drawable.BitmapDrawable;
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
    private TextView menuButton;
    private Button modeButton;
    private TextView webTool;
    private TextView thinkTool;
    private TextView imageTool;
    private String chatMode = "fast";
    private boolean webEnabled = false;
    private boolean thinkEnabled = false;
    private boolean imageEnabled = false;
    private Uri pendingAttachmentUri;
    private String pendingAttachmentName = "";
    private String pendingAttachmentMime = "";
    private String pendingAttachmentBase64 = "";
    private TextView attachmentChip;
    private Switch speakSwitch;
    private SpeechRecognizer speechRecognizer;
    private Dialog voiceCallDialog;
    private FrameLayout voiceCallOrb;
    private TextView voiceCallStatus;
    private boolean voiceCallActive = false;
    private boolean voiceCallSpeaking = false;
    private long speechStartedAt;
    private int voiceNoiseAttempts = 0;
    private boolean sending = false;
    private boolean ttsReady = false;
    private boolean micPermissionPending = false;
    private boolean voiceCallPermissionPending = false;
    private boolean dictating = false;
    private ImageButton micButton;
    private TextToSpeech tts;
    private Typeface uiTypeface;
    private Typeface uiBoldTypeface;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(9, 10, 15));
        getWindow().setNavigationBarColor(Color.rgb(9, 10, 15));
        prefs = getSharedPreferences("ai_tod4s3_private", MODE_PRIVATE);
        uiTypeface = Typeface.createFromAsset(getAssets(), "fonts/AiTod4S3-Regular.ttf");
        uiBoldTypeface = Typeface.createFromAsset(getAssets(), "fonts/AiTod4S3-Bold.ttf");
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
        Button registerButton = button("CREAR CUENTA  +", PANEL);
        registerButton.setTextSize(12);
        registerButton.setBackground(roundStroke(PANEL, 14, Color.rgb(78, 82, 98)));
        registerButton.setOnClickListener(v -> requestAccount());
        LinearLayout.LayoutParams registerParams = new LinearLayout.LayoutParams(fieldWidth, dp(48));
        registerParams.gravity = Gravity.CENTER_HORIZONTAL;
        registerParams.setMargins(0, dp(8), 0, 0);
        content.addView(registerButton, registerParams);
        status = label("▢  Acceso con la cuenta AiTod4S3 que te creó el administrador.", 12, false, Color.rgb(181, 188, 201));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(12), dp(12), dp(12), dp(12));
        status.setBackground(roundStroke(Color.argb(38, 255, 255, 255), 16, Color.argb(45, 255, 255, 255)));
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(fieldWidth, -2);
        statusParams.setMargins(0, dp(14), 0, 0);
        statusParams.gravity = Gravity.CENTER_HORIZONTAL;
        content.addView(status, statusParams);
        TextView community = label("Comunidad", 11, false, Color.rgb(121, 128, 147));
        community.setGravity(Gravity.CENTER);
        content.addView(community, margins(-1, -2, 0, 18, 0, 5));
        LinearLayout telegram = new LinearLayout(this);
        telegram.setGravity(Gravity.CENTER_VERTICAL);
        telegram.setPadding(dp(12), dp(9), dp(12), dp(9));
        telegram.setBackground(gradientRound(new int[]{Color.rgb(20, 55, 78), Color.rgb(18, 36, 55)}, 18));
        TextView tgIcon = label("➤", 22, true, Color.WHITE); tgIcon.setGravity(Gravity.CENTER);
        tgIcon.setBackground(round(Color.rgb(42, 171, 238), 22));
        telegram.addView(tgIcon, lp(42, 42));
        LinearLayout tgText = column();
        tgText.addView(label("Canal de Telegram", 14, true, Color.WHITE));
        tgText.addView(label("Novedades, anuncios y soporte", 11, false, Color.rgb(176, 204, 221)), margins(-1, -2, 0, 2, 0, 0));
        telegram.addView(tgText, new LinearLayout.LayoutParams(0, -2, 1f));
        TextView tgArrow = label("›", 28, false, Color.WHITE); tgArrow.setGravity(Gravity.CENTER);
        telegram.addView(tgArrow, lp(28, 42));
        telegram.setOnClickListener(v -> openTelegramChannel());
        content.addView(telegram, new LinearLayout.LayoutParams(fieldWidth, dp(62)));
        TextView footer = label("Conexión cifrada", 11, false, Color.rgb(121, 128, 147));
        footer.setGravity(Gravity.CENTER);
        content.addView(footer, margins(-1, -2, 0, 10, 0, 0));
        ScrollView loginScroll = new ScrollView(this);
        loginScroll.setFillViewport(true); loginScroll.setClipToPadding(false); loginScroll.setVerticalScrollBarEnabled(false);
        loginScroll.setBackgroundColor(Color.TRANSPARENT); loginScroll.addView(content, new ScrollView.LayoutParams(-1, -2));
        FrameLayout.LayoutParams contentParams = new FrameLayout.LayoutParams(
                Math.min(dp(390), getResources().getDisplayMetrics().widthPixels - dp(28)), -1, Gravity.CENTER);
        stage.addView(loginScroll, contentParams);
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

    private void requestAccount() {
        final String name = userField.getText().toString().trim();
        final String pass = passwordField.getText().toString();
        if (name.isEmpty() || pass.isEmpty()) { status.setText("Escribe el usuario y una contraseña de mínimo 10 caracteres."); return; }
        loginButton.setEnabled(false);
        status.setText("Enviando solicitud al administrador…");
        io.execute(() -> {
            try {
                JSONObject request = new JSONObject().put("username", name).put("password", pass).put("device_id", deviceId());
                post("/api/ai/register", null, request);
                runOnUiThread(() -> {
                    loginButton.setEnabled(true);
                    status.setText("Solicitud enviada. Cuando el administrador la apruebe, inicia sesión con estos datos.");
                });
            } catch (Exception e) {
                String message = friendlyError(e);
                runOnUiThread(() -> { loginButton.setEnabled(true); status.setText(message); });
            }
        });
    }

    private void showChat() {
        root = base();
        welcomePanel = null;
        LinearLayout page = column();
        page.setPadding(dp(14), dp(10), dp(14), dp(10));
        page.setBackgroundColor(Color.TRANSPARENT);
        LinearLayout header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        View headerSpacer = new View(this);
        header.addView(headerSpacer, lp(42, 42));
        modeButton = button("AiTod4S3 · Rápido⌄", Color.rgb(22, 24, 34));
        modeButton.setTextSize(12); modeButton.setGravity(Gravity.CENTER);
        modeButton.setBackground(roundStroke(Color.rgb(22, 24, 34), 16, Color.rgb(64, 68, 87)));
        modeButton.setOnClickListener(v -> showModeSheet());
        LinearLayout.LayoutParams modeParams = new LinearLayout.LayoutParams(0, dp(42), 1f);
        modeParams.setMargins(dp(8), 0, dp(8), 0); header.addView(modeButton, modeParams);
        callButton = iconButton(R.drawable.ic_call_voice, Color.WHITE, "Iniciar llamada de voz con AiTod4S3");
        callButton.setBackground(roundStroke(Color.rgb(29,31,40),15,Color.rgb(78,82,98)));
        callButton.setOnClickListener(v -> toggleVoiceCall());
        header.addView(callButton, lp(42,42));
        TextView newChat = label("✎", 20, true, Color.WHITE); newChat.setGravity(Gravity.CENTER);
        newChat.setContentDescription("Nuevo chat"); newChat.setBackground(roundStroke(Color.rgb(29,31,40), 15, Color.rgb(78,82,98)));
        newChat.setOnClickListener(v -> startNewChat());
        header.addView(newChat, lp(42, 42));
        page.addView(header, margins(-1, 48, 0, 0, 0, 8));
        scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setVerticalScrollBarEnabled(false);
        transcript = column(); transcript.setPadding(dp(2), dp(12), dp(2), dp(14));
        scroll.addView(transcript, new ScrollView.LayoutParams(-1, -2));
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
        if (history.isEmpty()) addWelcomeState(); else for (JSONObject item : history) addBubble(item.optString("content", ""), "user".equals(item.optString("role")));
        LinearLayout tools = new LinearLayout(this); tools.setGravity(Gravity.CENTER_VERTICAL);
        webTool = toolChip("◉  Web", false); thinkTool = toolChip("✦  Pensar", false); imageTool = null;
        tools.addView(webTool, new LinearLayout.LayoutParams(0, dp(34), 1f)); tools.addView(thinkTool, new LinearLayout.LayoutParams(0, dp(34), 1f));
        page.addView(tools, margins(-1, -2, 0, 4, 0, 2));
        attachmentChip = null;
        FrameLayout composerShell = new FrameLayout(this); composerShell.setPadding(dp(7), dp(6), dp(7), dp(6)); composerShell.setBackground(roundStroke(Color.rgb(11,12,18), 25, RED));
        LinearLayout composerRow = new LinearLayout(this); composerRow.setGravity(Gravity.CENTER_VERTICAL);
        composer = new EditText(this); composer.setSingleLine(false); composer.setMaxLines(5); composer.setHint("Pregúntale a AiTod4S3…"); composer.setTextColor(Color.rgb(244,245,249)); composer.setHintTextColor(Color.rgb(109,115,132)); composer.setTextSize(15); composer.setPadding(dp(7),0,dp(5),0); composer.setBackgroundColor(Color.TRANSPARENT); composer.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES | InputType.TYPE_TEXT_FLAG_MULTI_LINE); composer.setFilters(new InputFilter[]{new InputFilter.LengthFilter(5000)}); composer.setImeOptions(EditorInfo.IME_ACTION_SEND); composerRow.addView(composer, new LinearLayout.LayoutParams(0, dp(48), 1f));
        micButton = iconButton(R.drawable.ic_mic, Color.WHITE, "Dictar mensaje por voz"); micButton.setBackground(roundStroke(Color.rgb(31,33,42),22,Color.argb(52,255,255,255))); micButton.setOnClickListener(v -> { if (dictating) stopDictation(); else beginDictation(); }); composerRow.addView(micButton, lp(42,42));
        sendButton = iconButton(R.drawable.ic_send, Color.WHITE, "Enviar mensaje a AiTod4S3"); sendButton.setBackground(gradientRound(new int[]{RED,Color.rgb(193,18,46)},24)); sendButton.setOnClickListener(v -> sendMessage()); composerRow.addView(sendButton, lp(44,44)); composerShell.addView(composerRow, new FrameLayout.LayoutParams(-1,-1)); page.addView(composerShell, lp(-1,-2));
        composer.setOnEditorActionListener((view, actionId, event) -> { if (actionId == EditorInfo.IME_ACTION_SEND) { sendMessage(); return true; } return false; }); composer.setOnFocusChangeListener((view, focused) -> { if (focused) scroll.postDelayed(() -> scroll.fullScroll(View.FOCUS_DOWN), 180); });
        root.addView(page, new LinearLayout.LayoutParams(-1,0,1f));
    }

    private TextView toolChip(String text, boolean active) {
        TextView chip = label(text, 11, true, active ? Color.WHITE : Color.rgb(183,188,204));
        chip.setGravity(Gravity.CENTER); chip.setPadding(dp(7),0,dp(7),0); chip.setBackground(roundStroke(active ? Color.rgb(47,50,68) : Color.rgb(19,21,30), 16, active ? RED : Color.rgb(57,61,78)));
        chip.setOnClickListener(v -> { boolean on = v == webTool ? !webEnabled : v == thinkTool ? !thinkEnabled : !imageEnabled; if (v == webTool) webEnabled=on; else if (v == thinkTool) thinkEnabled=on; else imageEnabled=on; v.setBackground(roundStroke(on ? Color.rgb(47,50,68) : Color.rgb(19,21,30),16,on ? RED : Color.rgb(57,61,78))); });
        return chip;
    }
    private void showModeSheet() {
        LinearLayout panel=column(); panel.setPadding(dp(20),dp(18),dp(20),dp(12)); panel.setBackground(roundStroke(Color.rgb(18,20,28),20,Color.rgb(78,82,98)));
        panel.addView(label("Modo de respuesta",18,true,Color.WHITE), margins(-1,-2,0,0,0,12));
        TextView fast=label("Rápido\nRespuesta directa y ágil",14,true,Color.WHITE); fast.setPadding(dp(12),dp(10),dp(12),dp(10)); panel.addView(fast,lp(-1,58));
        TextView deep=label("Pensar a fondo\nRazona más antes de responder",14,true,Color.WHITE); deep.setPadding(dp(12),dp(10),dp(12),dp(10)); panel.addView(deep,margins(-1,58,0,8,0,0));
        Dialog d=new Dialog(this); d.setContentView(panel); d.show(); Window w=d.getWindow(); if(w!=null){w.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));w.setLayout(Math.min(dp(340),getResources().getDisplayMetrics().widthPixels-dp(28)),WindowManager.LayoutParams.WRAP_CONTENT);}
        fast.setOnClickListener(v->{chatMode="fast"; if(modeButton!=null)modeButton.setText("AiTod4S3 · Rápido⌄");d.dismiss();}); deep.setOnClickListener(v->{chatMode="deep";thinkEnabled=true;if(modeButton!=null)modeButton.setText("AiTod4S3 · Pensar a fondo⌄");d.dismiss();});
    }
    private void showAttachmentSheet() {
        LinearLayout panel=column(); panel.setPadding(dp(20),dp(18),dp(20),dp(12)); panel.setBackground(roundStroke(Color.rgb(18,20,28),20,Color.rgb(78,82,98))); panel.addView(label("Adjuntar",18,true,Color.WHITE),margins(-1,-2,0,0,0,12));
        for(String item:new String[]{"Cámara","Foto","Archivo"}){TextView row=label("＋  "+item,15,true,Color.WHITE);row.setGravity(Gravity.CENTER_VERTICAL);row.setPadding(dp(10),0,dp(10),0);panel.addView(row,lp(-1,50));row.setOnClickListener(v->{Intent pick=new Intent(Intent.ACTION_OPEN_DOCUMENT);pick.addCategory(Intent.CATEGORY_OPENABLE);pick.setType("Cámara".equals(item)?"image/*":"*/*");pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,false);try{startActivityForResult(pick,31);}catch(Exception e){Toast.makeText(this,"No se pudo abrir el selector de archivos.",Toast.LENGTH_LONG).show();}});}
        Dialog d=new Dialog(this);d.setContentView(panel);d.show();Window w=d.getWindow();if(w!=null){w.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));w.setLayout(Math.min(dp(330),getResources().getDisplayMetrics().widthPixels-dp(28)),WindowManager.LayoutParams.WRAP_CONTENT);}
    }
    private void showSideMenu() {
        LinearLayout panel=column();panel.setPadding(dp(18),dp(20),dp(18),dp(14));panel.setBackground(roundStroke(Color.rgb(11,13,20),22,Color.rgb(55,59,78)));
        LinearLayout brand=new LinearLayout(this);brand.setGravity(Gravity.CENTER_VERTICAL);brand.addView(createAiOrb(dp(42)),lp(42,42));brand.addView(label("AiTod4S3",20,true,Color.WHITE),margins(-1,-2,10,0,0,0));panel.addView(brand,lp(-1,48));
        EditText search=edit("Buscar chats",InputType.TYPE_CLASS_TEXT);panel.addView(search,margins(-1,48,0,14,0,0));
        search.addTextChangedListener(new android.text.TextWatcher(){ public void beforeTextChanged(CharSequence x,int a,int b,int c){} public void onTextChanged(CharSequence x,int a,int b,int c){ if(!history.isEmpty() && !history.toString().toLowerCase(Locale.ROOT).contains(x.toString().toLowerCase(Locale.ROOT))) Toast.makeText(AiActivity.this,"No hay coincidencias en esta conversación.",Toast.LENGTH_SHORT).show(); } public void afterTextChanged(android.text.Editable x){} });
        Button fresh=button("+  Nuevo chat",RED);fresh.setOnClickListener(v->{startNewChat();});panel.addView(fresh,lp(-1,50));
        panel.addView(label("PROYECTOS",11,true,Color.rgb(130,135,155)),margins(-1,-2,0,20,0,8));
        TextView cue=label("◈  Cue  ·  Conectar asistente",14,true,Color.WHITE); cue.setPadding(dp(8),0,dp(8),0); cue.setGravity(Gravity.CENTER_VERTICAL); cue.setBackground(roundStroke(Color.rgb(24,27,38),14,Color.rgb(57,61,78))); cue.setOnClickListener(v->{prefs.edit().putBoolean("cue_connector_enabled",true).apply(); Toast.makeText(this,"Conector Cue activado en esta app.",Toast.LENGTH_SHORT).show();}); panel.addView(cue,margins(-1,44,0,6,0,0));
        TextView sync=label("▱  Tod4S3 Sync",14,false,Color.WHITE); sync.setPadding(dp(8),0,dp(8),0); panel.addView(sync,lp(-1,34));
        panel.addView(label("HISTORIAL",11,true,Color.rgb(130,135,155)),margins(-1,-2,0,20,0,8));
        TextView current=label(history.isEmpty()?"Aún no hay mensajes":"Conversación actual",14,false,Color.WHITE); current.setPadding(dp(8),0,dp(8),0); current.setOnClickListener(v->{ showChat(); }); panel.addView(current,lp(-1,38));
        TextView memory=label("Memoria local: activa",12,true,Color.rgb(75,224,154)); memory.setPadding(dp(8),0,dp(8),0); panel.addView(memory,lp(-1,32));
        TextView tg=label("◉  Canal de Telegram   ›",14,true,Color.WHITE);tg.setPadding(dp(8),0,dp(8),0);tg.setGravity(Gravity.CENTER_VERTICAL);tg.setOnClickListener(v->openTelegramChannel());panel.addView(tg,margins(-1,48,0,16,0,0));
        Dialog d=new Dialog(this);d.setContentView(panel);d.setCancelable(true);d.show();Window w=d.getWindow();if(w!=null){w.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));w.setLayout(Math.min(dp(360),getResources().getDisplayMetrics().widthPixels-dp(20)),WindowManager.LayoutParams.MATCH_PARENT);}
    }
    private void openTelegramChannel(){
        String url="https://t.me/Dark_Zone_666"; try{Intent app=new Intent(Intent.ACTION_VIEW, Uri.parse(url));app.setPackage("org.telegram.messenger");startActivity(app);}catch(Exception ignored){try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));}catch(Exception e){Toast.makeText(this,"No se pudo abrir el canal de Telegram.",Toast.LENGTH_LONG).show();}}
    }

    private void prepareAttachment(Uri uri) {
        try {
            String mime = getContentResolver().getType(uri);
            if (mime == null) mime = "application/octet-stream";
            long size = 0; String name = uri.getLastPathSegment();
            try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
                if (c != null && c.moveToFirst()) { int ni=c.getColumnIndex(OpenableColumns.DISPLAY_NAME); int si=c.getColumnIndex(OpenableColumns.SIZE); if(ni>=0)name=c.getString(ni); if(si>=0 && !c.isNull(si))size=c.getLong(si); }
            }
            if (size > 2_000_000L) { Toast.makeText(this,"El archivo supera el máximo de 2 MB.",Toast.LENGTH_LONG).show(); return; }
            java.io.InputStream in=getContentResolver().openInputStream(uri); if(in==null) throw new Exception("open");
            java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream(); byte[] buffer=new byte[8192]; int n; while((n=in.read(buffer))!=-1){out.write(buffer,0,n);if(out.size()>2_000_000)throw new Exception("large");} in.close();
            pendingAttachmentUri=uri; pendingAttachmentName=name==null?"archivo":name; pendingAttachmentMime=mime; pendingAttachmentBase64=Base64.encodeToString(out.toByteArray(),Base64.NO_WRAP); imageEnabled=mime.startsWith("image/");
            if(imageTool!=null){imageTool.setText("▧  Imagen".equals(imageTool.getText())?"▧  Imagen lista":"▧  Archivo listo"); imageTool.setBackground(roundStroke(Color.rgb(47,50,68),16,RED));}
            if(attachmentChip!=null){attachmentChip.setText("Adjunto: "+pendingAttachmentName+"  ·  Toca para quitar");attachmentChip.setVisibility(View.VISIBLE);}
            Toast.makeText(this,"Adjunto listo para enviar.",Toast.LENGTH_SHORT).show();
        } catch(Exception e){Toast.makeText(this,"No se pudo leer el archivo o supera 2 MB.",Toast.LENGTH_LONG).show();}
    }
    private void clearAttachment(){ pendingAttachmentUri=null;pendingAttachmentName="";pendingAttachmentMime="";pendingAttachmentBase64="";if(attachmentChip!=null)attachmentChip.setVisibility(View.GONE);if(imageTool!=null){imageTool.setText("▧  Imagen");imageTool.setBackground(roundStroke(Color.rgb(19,21,30),16,Color.rgb(57,61,78)));} }

    private void sendMessage() {
        if (sending || composer == null) return;
        String text = composer.getText().toString().trim();
        if (text.isEmpty() && pendingAttachmentBase64.isEmpty()) return;
        final String attachmentName = pendingAttachmentName;
        final String attachmentMime = pendingAttachmentMime;
        final String attachmentBase64 = pendingAttachmentBase64;
        composer.setText("");
        clearAttachment();
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
        JSONArray remembered = new JSONArray();
        try { remembered = new JSONArray(prefs.getString("memory_" + username, "[]")); } catch (Exception ignored) {}
        for (int i = Math.max(0, remembered.length() - 4); i < remembered.length(); i++) messages.put(remembered.optJSONObject(i));
        int from = Math.max(0, history.size() - Math.max(1, 12 - messages.length()));
        for (int i = from; i < history.size(); i++) messages.put(history.get(i));
        io.execute(() -> {
            try {
                JSONObject requestPayload = new JSONObject().put("messages", messages).put("mode", chatMode).put("web_search", webEnabled).put("deep_thinking", thinkEnabled);
                // AiTod4S3 funciona en esta versión únicamente como chat de texto.
                JSONObject response = post("/api/ai/chat", token, requestPayload);
                String reply = response.optString("reply", "");
                if (reply.isEmpty()) throw new Exception("La IA devolvió una respuesta vacía.");
                runOnUiThread(() -> {
                    removeThinkingBubble();
                    try {
                        JSONObject assistant = new JSONObject().put("role", "assistant").put("content", reply);
                        history.add(assistant);
                        JSONArray rememberedHistory = new JSONArray(); try { rememberedHistory = new JSONArray(prefs.getString("memory_" + username, "[]")); } catch (Exception ignored) {}
                        rememberedHistory.put(userMessage); rememberedHistory.put(assistant); while (rememberedHistory.length() > 12) rememberedHistory.remove(0); prefs.edit().putString("memory_" + username, rememberedHistory.toString()).apply();
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
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            Toast.makeText(this, "No hay un servicio de reconocimiento de voz disponible.", Toast.LENGTH_LONG).show(); return;
        }
        if (speechRecognizer == null) speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
        speechRecognizer.cancel();
        dictating = true;
        if (micButton != null) { micButton.setContentDescription("Detener dictado"); micButton.setBackground(roundStroke(Color.rgb(75,45,55),22,RED)); }
        Toast.makeText(this,"Escuchando… habla ahora",Toast.LENGTH_SHORT).show();
        speechRecognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) { if (chatStatus != null) chatStatus.setText("escuchando…"); }
            @Override public void onBeginningOfSpeech() { speechStartedAt = SystemClock.uptimeMillis(); }
            @Override public void onRmsChanged(float rms) { }
            @Override public void onBufferReceived(byte[] buffer) { }
            @Override public void onEndOfSpeech() { if (chatStatus != null) chatStatus.setText("procesando…"); }
            @Override public void onError(int error) { dictating=false; if (micButton != null) { micButton.setContentDescription("Dictar mensaje por voz"); micButton.setBackground(roundStroke(Color.rgb(31,33,42),22,Color.argb(52,255,255,255))); } if (chatStatus != null) chatStatus.setText("en línea"); }
            @Override public void onResults(Bundle results) {
                ArrayList<String> values = results == null ? null : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                String phrase = values == null || values.isEmpty() ? "" : values.get(0);
                if (isUsableVoicePhrase(phrase, results) && composer != null) {
                    composer.setText(phrase.trim()); composer.setSelection(composer.length());
                }
                dictating=false; if (micButton != null) { micButton.setContentDescription("Dictar mensaje por voz"); micButton.setBackground(roundStroke(Color.rgb(31,33,42),22,Color.argb(52,255,255,255))); }
                if (chatStatus != null) chatStatus.setText("en línea");
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

    private void stopDictation() { if (speechRecognizer != null) speechRecognizer.stopListening(); dictating=false; if (micButton != null) { micButton.setContentDescription("Dictar mensaje por voz"); micButton.setBackground(roundStroke(Color.rgb(31,33,42),22,Color.argb(52,255,255,255))); } if (chatStatus != null) chatStatus.setText("en línea"); }

    private void startNewChat() { if (sending) { Toast.makeText(this,"Espera a que termine la respuesta o deténla.",Toast.LENGTH_SHORT).show(); return; } history.clear(); saveHistory(); chatMode="fast"; webEnabled=false; thinkEnabled=false; imageEnabled=false; showChat(); Toast.makeText(this,"Nuevo chat listo. La memoria guardada se conserva.",Toast.LENGTH_SHORT).show(); }

    private boolean isUsableVoicePhrase(String phrase, Bundle results) {
        if (phrase == null) return false;
        String normalized = phrase.toLowerCase(Locale.ROOT).replaceAll("[^a-záéíóúüñ0-9 ]", " ").replaceAll("\\s+", " ").trim();
        if (normalized.length() < 2 || normalized.matches("(?:eh|ah|ajá|mmm+|mm+|uh+|um+|hm+|hmm+|oh+|em+)")) return false;
        if (results != null) { float[] confidence = results.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES); if (confidence != null && confidence.length > 0 && confidence[0] < .30f && normalized.length() < 8) return false; }
        return true;
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == 18 && micPermissionPending) {
            micPermissionPending = false;
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) launchRecognizer();
            else Toast.makeText(this, "Permite el micrófono para dictar; también puedes escribir.", Toast.LENGTH_LONG).show();
        } else if (request == 18 && voiceCallPermissionPending) {
            voiceCallPermissionPending = false;
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) toggleVoiceCall();
            else Toast.makeText(this, "Permite el micrófono para usar la llamada de voz.", Toast.LENGTH_LONG).show();
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 31 && result == RESULT_OK && data != null && data.getData() != null) {
            prepareAttachment(data.getData());
            return;
        }
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
        TextView title = label("Hola, " + (username.isEmpty() ? "" : username) + ".\n¿En qué te ayudo hoy?", 25, true, Color.WHITE);
        title.setGravity(Gravity.CENTER);
        welcomePanel.addView(title, margins(-1, -2, 0, 16, 0, 4));
        TextView subtitle = label("Tu asistente inteligente", 14, false, Color.rgb(170, 176, 194));
        subtitle.setGravity(Gravity.CENTER);
        welcomePanel.addView(subtitle, margins(-1, -2, 0, 0, 0, 20));

        String[] titles = {"Escribir un texto", "Ayuda con código", "Analizar una foto", "Buscar en la web"};
        String[] hints = {"Redacta desde cero", "Soluciona y explica", "Sube una imagen", "Encuentra fuentes"};
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
            LinearLayout actions = new LinearLayout(this); actions.setGravity(Gravity.CENTER_VERTICAL);
            TextView copy = label("Copiar", 11, true, Color.rgb(150,156,177)); copy.setPadding(dp(7),0,dp(7),0);
            copy.setOnClickListener(v -> { ClipboardManager cb=(ClipboardManager)getSystemService(CLIPBOARD_SERVICE); if(cb!=null) cb.setPrimaryClip(ClipData.newPlainText("AiTod4S3", text)); Toast.makeText(this,"Respuesta copiada",Toast.LENGTH_SHORT).show(); }); actions.addView(copy, lp(-2,30));
            TextView like = label("Me gusta", 11, true, Color.rgb(150,156,177)); like.setPadding(dp(7),0,dp(7),0); like.setOnClickListener(v->Toast.makeText(this,"Valoración guardada",Toast.LENGTH_SHORT).show()); actions.addView(like,lp(-2,30));
            TextView dislike = label("No me gusta", 11, true, Color.rgb(150,156,177)); dislike.setPadding(dp(7),0,dp(7),0); dislike.setOnClickListener(v->Toast.makeText(this,"Valoración guardada",Toast.LENGTH_SHORT).show()); actions.addView(dislike,lp(-2,30));
            TextView regen = label("Regenerar", 11, true, Color.rgb(150,156,177)); regen.setPadding(dp(7),0,dp(7),0); regen.setOnClickListener(v->{ if(!history.isEmpty()){ history.remove(history.size()-1); String prompt=history.isEmpty()?"":history.get(history.size()-1).optString("content",""); saveHistory(); showChat(); if(composer!=null){composer.setText(prompt); sendMessage();} }}); actions.addView(regen,lp(-2,30));
            TextView speak = label("Leer", 11, true, Color.rgb(150,156,177)); speak.setPadding(dp(7),0,dp(7),0); speak.setOnClickListener(v->{ensureTts(); if(tts!=null)tts.speak(text,TextToSpeech.QUEUE_FLUSH,null,"aitod4s3-action");}); actions.addView(speak,lp(-2,30));
            answer.addView(actions, margins(-1, -2, 0, 0, 0, 0));
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

    private void showChatOptions(View anchor) {
        LinearLayout panel = column(); panel.setPadding(dp(20), dp(16), dp(20), dp(12));
        panel.setBackground(roundStroke(Color.rgb(18, 20, 28), 20, Color.rgb(78, 82, 98)));
        TextView title = label("Opciones del chat", 18, true, Color.WHITE); panel.addView(title, margins(-1, -2, 0, 0, 0, 10));
        TextView clear = label("Limpiar conversación", 14, true, Color.rgb(255, 145, 160)); clear.setGravity(Gravity.CENTER_VERTICAL);
        clear.setPadding(dp(12), 0, dp(12), 0); clear.setBackground(roundStroke(Color.rgb(44, 26, 34), 14, Color.rgb(126, 54, 72)));
        panel.addView(clear, lp(-1, 48));
        TextView close = label("Cerrar", 13, true, Color.rgb(170, 176, 194)); close.setGravity(Gravity.CENTER);
        panel.addView(close, margins(-1, 46, 0, 8, 0, 0));
        Dialog dialog = new Dialog(this); dialog.setContentView(panel); dialog.setCancelable(true); dialog.show();
        Window window = dialog.getWindow();
        if (window != null) { window.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT)); window.setLayout(Math.min(dp(330), getResources().getDisplayMetrics().widthPixels - dp(32)), WindowManager.LayoutParams.WRAP_CONTENT); }
        clear.setOnClickListener(v -> { history.clear(); saveHistory(); renderEmptyChat(); dialog.dismiss(); });
        close.setOnClickListener(v -> dialog.dismiss());
    }

    private void renderEmptyChat() {
        if (transcript == null) return;
        transcript.removeAllViews(); welcomePanel = null; addWelcomeState();
    }

    private void toggleVoiceCall() {
        if (voiceCallActive) { stopVoiceCall(); return; }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            voiceCallPermissionPending = true;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 18); return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            Toast.makeText(this, "El dictado de voz no está disponible en este teléfono.", Toast.LENGTH_LONG).show(); return;
        }
        voiceCallActive = true;
        voiceNoiseAttempts = 0;
        if (callButton != null) callButton.setBackground(gradientRound(new int[]{RED, Color.rgb(193, 18, 46)}, 15));
        showVoiceCallDialog(); beginVoiceRecognition();
    }

    private void beginVoiceRecognition() {
        if (!voiceCallActive) return;
        if (speechRecognizer == null) speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
        speechRecognizer.cancel();
        dictating = true;
        if (micButton != null) { micButton.setContentDescription("Detener dictado"); micButton.setBackground(roundStroke(Color.rgb(75,45,55),22,RED)); }
        Toast.makeText(this,"Escuchando… habla ahora",Toast.LENGTH_SHORT).show();
        speechRecognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) { setVoiceCallStatus("ESCUCHANDO…"); }
            @Override public void onBeginningOfSpeech() { speechStartedAt = SystemClock.uptimeMillis(); }
            @Override public void onRmsChanged(float rms) { }
            @Override public void onBufferReceived(byte[] buffer) { }
            @Override public void onEndOfSpeech() { setVoiceCallStatus("PROCESANDO…"); }
            @Override public void onError(int error) {
                if (voiceCallActive && !sending && voiceNoiseAttempts++ < 2) postDelayed(() -> beginVoiceRecognition(), 260);
                else if (voiceCallActive) setVoiceCallStatus("NO SE DETECTÓ VOZ · HABLA DE NUEVO");
            }
            @Override public void onResults(Bundle results) {
                ArrayList<String> values = results == null ? null : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                String phrase = values == null || values.isEmpty() ? "" : values.get(0).trim();
                long duration = speechStartedAt == 0 ? 1000 : SystemClock.uptimeMillis() - speechStartedAt;
                if (isUsableVoicePhrase(phrase, results) && (duration >= 180 || phrase.length() >= 5) && composer != null && voiceCallActive) {
                    voiceNoiseAttempts = 0; composer.setText(phrase); composer.setSelection(composer.length()); setVoiceCallStatus("CONECTANDO CON LA IA…"); sendMessage();
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

    private void ensureTts() {
        if (tts == null) tts = new TextToSpeech(this, result -> { if (result == TextToSpeech.SUCCESS) { tts.setLanguage(new Locale("es", "ES")); ttsReady = true; } });
    }

    private void postDelayed(Runnable action, long delay) { getWindow().getDecorView().postDelayed(action, delay); }

    private String friendlyError(Exception e) {
        String raw = e.getMessage() == null ? "" : e.getMessage();
        if (raw.contains("invalid_credentials")) return "Usuario o contraseña incorrectos.";
        if (raw.contains("device_in_use")) return "Esta cuenta ya está vinculada a otro dispositivo. Pide al administrador que libere el dispositivo anterior.";
        if (raw.contains("invalid_device")) return "No se pudo identificar este dispositivo Android.";
        if (raw.contains("unauthorized")) return "La sesión venció. Inicia sesión otra vez.";
        if (raw.contains("login_rate_limited")) return "Demasiados intentos. Espera unos minutos.";
        if (raw.contains("account_exists")) return "Ese usuario ya tiene una cuenta AiTod4S3 activa.";
        if (raw.contains("request_exists")) return "Ya existe una solicitud pendiente para ese usuario.";
        if (raw.contains("invalid_username")) return "Usuario inválido: usa 3–32 letras, números, punto, guion o guion bajo.";
        if (raw.contains("invalid_password")) return "La contraseña debe tener entre 10 y 256 caracteres.";
        if (raw.contains("ai_rate_limited")) return "Espera un momento antes de enviar otro mensaje.";
        if (raw.contains("ai_not_configured")) return "La IA no está configurada temporalmente. El respaldo automático también está iniciándose.";
        if (raw.contains("ai_fallback_unavailable")) return "La IA principal y su respaldo web no responden ahora. Inténtalo de nuevo más tarde.";
        if (raw.contains("ai_unavailable")) return "La IA no está disponible temporalmente.";
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
        BitmapDrawable backdrop = new BitmapDrawable(getResources(), BitmapFactory.decodeResource(getResources(), R.drawable.bg_aitod4s3));
        backdrop.setGravity(Gravity.FILL); view.setBackground(backdrop);
        setContentView(view);
        return view;
    }
    private LinearLayout column() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); return l; }
    private TextView label(String text, int size, boolean bold, int color) {
        TextView v = new TextView(this); v.setText(text); v.setTextSize(size); v.setTextColor(color);
        if (bold && uiBoldTypeface != null) v.setTypeface(uiBoldTypeface); else if (uiTypeface != null) v.setTypeface(uiTypeface);
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
        if (speechRecognizer != null) { speechRecognizer.cancel(); speechRecognizer.destroy(); speechRecognizer = null; }
        if (tts != null) { tts.stop(); tts.shutdown(); }
        io.shutdownNow();
        super.onDestroy();
    }

}
