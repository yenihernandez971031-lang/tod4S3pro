# AiTod4S3 independiente

## Qué incluye

El proyecto ahora tiene un módulo Android independiente, `:ai-app`, con `applicationId` `com.tod4s3.ai`. La app ofrece inicio de sesión propio, conversación local por usuario, dictado en español por micrófono y respuestas habladas mediante el TTS del dispositivo. No incluye ToDus, descargas ni pantallas de Tod4S3 Sync, no usa el emparejamiento de ToDus y no requiere Premium.

La conversación usa `/api/ai/chat`, separado de `/api/app/ai/chat`. Por tanto, el requisito Premium de Tod4S3 Sync permanece intacto y no se aplica a la APK nueva.

## Crear y administrar cuentas

Un administrador del bot, desde el chat privado, utiliza:

```text
/crear_ia usuario contraseña
```

El nombre de usuario debe tener 3–32 caracteres (letras ASCII, números, punto, guion o guion bajo); la contraseña debe tener al menos 10 caracteres. El bot intenta borrar el mensaje de comando antes de crear la cuenta y no la crea si no puede borrarlo. No envía ni repite la contraseña en su respuesta. Ejecutar `/crear_ia` otra vez con el mismo usuario reemplaza la contraseña y revoca sus sesiones.

Para desactivar una cuenta y revocar sus sesiones:

```text
/desactivar_ia usuario
```

Ambos comandos son privados y requieren permisos de administrador del bot. Entrega las credenciales al usuario final por un canal privado; no las publiques en grupos.

Cada cuenta queda vinculada al primer Android que inicia sesión. El backend guarda un hash del Android ID, y cada chat comprueba que el token y el dispositivo coincidan; iniciar sesión desde otro teléfono devuelve `device_in_use`. Si el usuario pierde/cambia el teléfono, un administrador libera el dispositivo anterior en privado:

```text
/liberar_ia usuario
```

Eso revoca sus sesiones y permite que el siguiente Android que inicie sesión quede vinculado. El usuario puede cerrar y volver a abrir sesión en su mismo dispositivo sin perder el vínculo.

## Almacenamiento y seguridad

Las cuentas se guardan en `AI_ACCOUNTS_FILE`, cuyo valor predeterminado en el bot es `ai_app_accounts.json` dentro de `DATA_DIR` (en Railway debe ser un volumen persistente). Las contraseñas se verifican con PBKDF2-HMAC-SHA256 y sal aleatoria; la contraseña no se almacena en claro. El archivo se escribe atómicamente con permisos restrictivos. Los tokens aleatorios de sesión duran 30 días y se guarda solo su hash; reemplazar o desactivar una cuenta revoca sus sesiones. La vinculación por Android ID impide el uso normal simultáneo desde otro teléfono; no equivale a atestación de hardware y un dispositivo rooteado con herramientas para falsificar identificadores podría evadirla.

El chat limita cada cuenta a 8 solicitudes/minuto y 80/día por defecto (`AI_APP_DAILY_LIMIT` puede ajustarse). El endpoint móvil usa HTTPS fijo. Si `IASINCENSURA` está configurada, se usa primero la API principal; si falta o falla, el backend usa automáticamente el chat web de NoTrack como respaldo, sin exponer claves en la APK.

## Compilación

Desde `sync-app/`:

```bash
./gradlew :ai-app:assembleDebug
```

La APK de depuración se genera en `ai-app/build/outputs/apk/debug/ai-app-debug.apk`. Para distribuirla se debe compilar el tipo release y firmar con el almacén de firma autorizado. La compilación/publicación no se considera verificada hasta tener Android SDK instalado, ejecutar pruebas en dispositivo y confirmar firma/certificado.

## Rutas

- `POST /api/ai/login`
- `POST /api/ai/logout`
- `POST /api/ai/chat`
- Archivo de actividad: `sync-app/ai-app/src/main/java/com/tod4s3/ai/AiActivity.java`
