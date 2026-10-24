# Mis noticias (gratis, con GitHub Pages)

Cada mañana GitHub lee tus fuentes RSS y publica una página web con los titulares. No cuesta nada.

## Puesta en marcha (5 minutos)

1. Crea una cuenta en github.com (gratis) y un repositorio nuevo, por ejemplo `noticias`.
   - Si quieres que nadie más vea la página, usa un repositorio **público** pero no compartas el enlace
     (las páginas privadas de GitHub Pages requieren plan de pago).
2. Sube todo el contenido de esta carpeta al repositorio (botón **Add file → Upload files**).
   Asegúrate de que se sube también la carpeta oculta `.github`.
3. En el repositorio: **Settings → Pages → Source: GitHub Actions**.
4. Ve a la pestaña **Actions → Actualizar noticias → Run workflow** para generar la primera versión.
5. Tu página quedará en `https://TU_USUARIO.github.io/noticias/`. Añádela a favoritos en el móvil.

A partir de ahí se actualiza sola cada día sobre las 06:30 (hora de Madrid).

## Personalizar

Edita `feeds.json` (se puede hacer desde la web de GitHub, con el lápiz):

- `max_por_fuente`: titulares por fuente. `max_dias`: antigüedad máxima.
- Para añadir una fuente: una línea nueva con `nombre` y `url` del feed RSS.
- `filtro`: opcional; solo se muestran titulares que contengan alguna de esas palabras.
- Google Noticias sirve de RSS para cualquier búsqueda:
  `https://news.google.com/rss/search?q=TU+BUSQUEDA+when:2d&hl=es&gl=ES&ceid=ES:es`

Si una fuente falla, la página lo indica en su tarjeta y el resto sigue funcionando.
