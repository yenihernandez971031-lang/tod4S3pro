package com.tod4s3.sync;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Minimal native HTTP authentication client for ToDus. */
final class ToDusAuth {
    private static final String AUTH_BASE = "https://auth.todus.cu/v2/auth/";
    private static final String AUTH_VERSION_NAME = "2.1.2";
    private static final String AUTH_VERSION_CODE = "30102";
    private static final String ALPHANUMERIC = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Pattern JWT_PATTERN = Pattern.compile("eyJ[a-zA-Z0-9_-]+\\.[a-zA-Z0-9_-]+\\.[a-zA-Z0-9_-]+");
    private static final Pattern PASSWORD_PATTERN = Pattern.compile("[a-fA-F0-9]{32,256}");
    private static final Pattern JSON_SESSION_PATTERN = Pattern.compile("\"(?:password|session_password|session|token)\"\\s*:\\s*\"([A-Za-z0-9._~+/=-]{32,256})\"");
    private static final Pattern SAFE_SESSION_PATTERN = Pattern.compile("[A-Za-z0-9._~+\\/=\\-]{32,256}");

    private ToDusAuth() { }

    static final class Session {
        final String phone;
        final String password;
        final String token;

        Session(String phone, String password, String token) {
            this.phone = phone;
            this.password = password;
            this.token = token;
        }
    }

    static String requestPin(String phoneInput) throws Exception {
        String phone = normalizePhone(phoneInput);
        ByteArrayOutputStream payload = new ByteArrayOutputStream();
        writeStringField(payload, 1, phone);
        writeStringField(payload, 2, randomToken(150));
        post("users.reserve", payload.toByteArray());
        return phone;
    }

    static Session validateAndLogin(String phoneInput, String pinInput) throws Exception {
        String phone = normalizePhone(phoneInput);
        String pin = pinInput == null ? "" : pinInput.trim();
        if (!pin.matches("[0-9]{4,8}")) {
            throw new IOException("El PIN debe contener entre 4 y 8 dígitos.");
        }

        ByteArrayOutputStream payload = new ByteArrayOutputStream();
        writeStringField(payload, 1, phone);
        writeStringField(payload, 2, randomToken(150));
        writeStringField(payload, 3, pin);
        byte[] response = post("users.register", payload.toByteArray());
        String password = extractSessionPassword(response);
        String token = login(phone, password);
        return new Session(phone, password, token);
    }

    static String login(String phoneInput, String passwordInput) throws Exception {
        String phone = normalizePhone(phoneInput);
        String password = passwordInput == null ? "" : passwordInput.trim();
        if (password.isEmpty()) throw new IOException("No hay una clave de sesión ToDus guardada.");

        ByteArrayOutputStream payload = new ByteArrayOutputStream();
        writeStringField(payload, 1, phone);
        writeStringField(payload, 2, password);
        writeStringField(payload, 3, randomToken(150));
        writeStringField(payload, 4, AUTH_VERSION_CODE);
        byte[] response = post("token", payload.toByteArray());
        String raw = new String(response, StandardCharsets.UTF_8).trim();

        Matcher jwt = JWT_PATTERN.matcher(raw);
        if (jwt.find()) return jwt.group();

        String allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-_~+/";
        StringBuilder token = new StringBuilder();
        for (int i = 0; i < raw.length(); i++) {
            char ch = raw.charAt(i);
            if (allowed.indexOf(ch) >= 0) token.append(ch);
        }
        if (token.length() == 0) throw new IOException("ToDus no devolvió un token de sesión válido.");
        return token.toString();
    }

    private static String normalizePhone(String input) throws IOException {
        String raw = input == null ? "" : input.trim();
        if (!raw.matches("\\+?[0-9\\s()\\-]+")) {
            throw new IOException("Escribe un número de teléfono válido.");
        }
        String digits = raw.replaceAll("[^0-9]", "");
        if (digits.startsWith("00")) digits = digits.substring(2);
        if (digits.length() == 8) return "53" + digits;
        if (digits.length() == 10 && digits.startsWith("53")) return digits;
        throw new IOException("Escribe el número ToDus con 8 dígitos o con el prefijo 53.");
    }

    private static String extractSessionPassword(byte[] response) throws IOException {
        if (response == null || response.length == 0) throw new IOException("ToDus no devolvió la clave de sesión.");
        String latin = new String(response, StandardCharsets.ISO_8859_1);
        Matcher json = JSON_SESSION_PATTERN.matcher(latin);
        if (json.find() && isSessionPassword(json.group(1))) return json.group(1);
        Matcher hexPassword = PASSWORD_PATTERN.matcher(latin);
        if (hexPassword.find() && isSessionPassword(hexPassword.group())) return hexPassword.group();
        for (int i = 0; i < response.length; i++) {
            if (response[i] == '`') {
                int end = i + 1;
                while (end < response.length && response[end] != '`' && end - i <= 257) end++;
                String candidate = printableTrim(response, i + 1, end - i - 1);
                if (isSessionPassword(candidate)) return candidate;
            }
        }
        String protobufCandidate = findLengthDelimitedPassword(response);
        if (!protobufCandidate.isEmpty()) return protobufCandidate;
        Matcher safe = SAFE_SESSION_PATTERN.matcher(latin);
        while (safe.find()) {
            String candidate = safe.group();
            if (isSessionPassword(candidate)) return candidate;
        }
        if (response.length >= 32) {
            String printable = printableTrim(response, 0, response.length);
            if (isSessionPassword(printable)) return printable;
        }
        throw new IOException("No se pudo interpretar la clave de sesión que devolvió ToDus.");
    }

    private static String findLengthDelimitedPassword(byte[] response) {
        int offset = 0;
        while (offset < response.length) {
            int tag = readVarint(response, offset);
            int tagBytes = varintSize(response, offset);
            if (tag < 0 || tagBytes == 0) break;
            offset += tagBytes;
            int wireType = tag & 7;
            if (wireType != 2) {
                int skipped = skipWireValue(response, offset, wireType);
                if (skipped < 0) break;
                offset = skipped;
                continue;
            }
            int length = readVarint(response, offset);
            int lengthBytes = varintSize(response, offset);
            if (length < 0 || lengthBytes == 0) break;
            offset += lengthBytes;
            if (length > response.length - offset) break;
            String candidate = printableTrim(response, offset, length);
            if (isSessionPassword(candidate)) return candidate;
            offset += length;
        }
        return "";
    }

    private static int readVarint(byte[] data, int offset) {
        long value = 0L;
        int shift = 0;
        for (int i = offset; i < data.length && i < offset + 5; i++) {
            int b = data[i] & 0xff;
            value |= (long) (b & 0x7f) << shift;
            if ((b & 0x80) == 0) return value > Integer.MAX_VALUE ? -1 : (int) value;
            shift += 7;
        }
        return -1;
    }

    private static int varintSize(byte[] data, int offset) {
        if (offset >= data.length) return 0;
        for (int i = offset; i < data.length && i < offset + 5; i++) {
            if ((data[i] & 0x80) == 0) return i - offset + 1;
        }
        return 0;
    }

    private static int skipWireValue(byte[] data, int offset, int wireType) {
        if (wireType == 0) {
            int size = varintSize(data, offset);
            return size == 0 ? -1 : offset + size;
        }
        if (wireType == 1) return offset + 8 <= data.length ? offset + 8 : -1;
        if (wireType == 5) return offset + 4 <= data.length ? offset + 4 : -1;
        return -1;
    }

    private static String printableTrim(byte[] data, int offset, int length) {
        if (offset < 0 || length < 0 || offset > data.length - length) return "";
        String value = new String(data, offset, length, StandardCharsets.UTF_8).trim();
        for (int i = 0; i < value.length(); i++) {
            if (Character.isISOControl(value.charAt(i)) && !Character.isWhitespace(value.charAt(i))) return "";
        }
        return value;
    }

    private static boolean isSessionPassword(String candidate) {
        if (candidate == null || candidate.length() < 32 || candidate.length() > 256 || candidate.matches(".*\\s.*")) return false;
        for (int i = 0; i < candidate.length(); i++) {
            char ch = candidate.charAt(i);
            if (!(Character.isLetterOrDigit(ch) || ".-_~+/=".indexOf(ch) >= 0)) return false;
        }
        return true;
    }

    private static byte[] post(String endpoint, byte[] payload) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(AUTH_BASE + endpoint).openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(12_000);
        connection.setReadTimeout(18_000);
        connection.setDoOutput(true);
        connection.setRequestProperty("User-Agent", "ToDus " + AUTH_VERSION_NAME + " Auth");
        connection.setRequestProperty("Content-Type", "application/x-protobuf");
        connection.setRequestProperty("Accept", "*/*");
        connection.setFixedLengthStreamingMode(payload.length);
        try {
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload);
            }
            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            byte[] body = stream == null ? new byte[0] : readAll(stream);
            if (status == 404 && "users.register".equals(endpoint)) {
                throw new IOException("ToDus devolvió HTTP 404 al validar el PIN. Pulsa «Reenviar código» y usa el PIN más reciente. Si el error continúa, contacta con el soporte de ToDus; no compartas tu PIN.");
            }
            if (status == 403) {
                throw new IOException("ToDus no aceptó la clave de sesión (HTTP 403). Solicita un PIN nuevo.");
            }
            if (status < 200 || status >= 300) {
                throw new IOException("auth.todus.cu/v2/auth/" + endpoint + " respondió HTTP " + status + ".");
            }
            return body;
        } finally {
            connection.disconnect();
        }
    }

    private static byte[] readAll(InputStream stream) throws IOException {
        try (InputStream in = stream; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            return out.toByteArray();
        }
    }

    private static void writeStringField(ByteArrayOutputStream output, int fieldNumber, String value) throws IOException {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        writeVarint(output, (fieldNumber << 3) | 2);
        writeVarint(output, bytes.length);
        output.write(bytes);
    }

    private static void writeVarint(ByteArrayOutputStream output, int value) {
        while ((value & 0xFFFFFF80) != 0) {
            output.write((value & 0x7F) | 0x80);
            value >>>= 7;
        }
        output.write(value & 0x7F);
    }

    private static String randomToken(int length) {
        StringBuilder token = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            token.append(ALPHANUMERIC.charAt(RANDOM.nextInt(ALPHANUMERIC.length())));
        }
        return token.toString();
    }
}
