
package org.kivy;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.os.Build;
import android.webkit.JavascriptInterface;


// Ponte JS -> Android nativo pra mostrar notificação com a cor de marca
// (o Chrome/Web Push não deixa customizar essa cor, então isso só funciona
// aqui dentro do app empacotado via buildozer, chamando a API nativa direto).
public class NativeNotifier {

    private static final String CHANNEL_ID = "sims4_novidades";

    private final Context context;

    public NativeNotifier(Context context) {
        this.context = context;
    }

    @JavascriptInterface
    public void notify(String title, String body, String colorHex, String tag, String url) {
        try {
            NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager == null) return;

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                NotificationChannel channel = manager.getNotificationChannel(CHANNEL_ID);
                if (channel == null) {
                    channel = new NotificationChannel(
                        CHANNEL_ID, "Novidades", NotificationManager.IMPORTANCE_DEFAULT
                    );
                    channel.setDescription("Avisos e novidades do Sims 4 Translator");
                    manager.createNotificationChannel(channel);
                }
            }

            int color;
            try {
                color = Color.parseColor(colorHex);
            } catch (Exception e) {
                color = Color.parseColor("#b89cd9"); // roxinho padrão, caso venha algo inválido
            }

            int smallIconId = context.getResources().getIdentifier("icon", "mipmap", context.getPackageName());
            if (smallIconId == 0) {
                smallIconId = context.getResources().getIdentifier("icon", "drawable", context.getPackageName());
            }
            if (smallIconId == 0) {
                smallIconId = android.R.drawable.ic_dialog_info;
            }

            Intent launchIntent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
            PendingIntent contentIntent = null;
            if (launchIntent != null) {
                launchIntent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
                launchIntent.putExtra("notify_url", url);
                int flags = PendingIntent.FLAG_UPDATE_CURRENT;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    flags |= PendingIntent.FLAG_IMMUTABLE;
                }
                contentIntent = PendingIntent.getActivity(context, 0, launchIntent, flags);
            }

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(context, CHANNEL_ID);
            } else {
                builder = new Notification.Builder(context);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                    builder.setPriority(Notification.PRIORITY_DEFAULT);
                }
            }

            builder.setContentTitle(title)
                   .setContentText(body)
                   .setSmallIcon(smallIconId)
                   .setColor(color)
                   .setAutoCancel(true);

            if (contentIntent != null) {
                builder.setContentIntent(contentIntent);
            }

            int notifyId = (tag != null ? tag : "update").hashCode();
            manager.notify(notifyId, builder.build());
        } catch (Exception e) {
            // Silencioso de propósito: se a notificação nativa falhar por qualquer
            // motivo, o fallback (Web Push padrão do Chrome/WebView) continua valendo.
        }
    }
}
