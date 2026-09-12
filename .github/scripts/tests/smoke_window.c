/* A real GTK window exercises the smoke runner's process and pixel checks. */
#include <gtk/gtk.h>
#include <stdlib.h>
#include <string.h>

static gboolean abort_after_drawing(gpointer unused) {
  (void)unused;
  abort();
}

int main(int argc, char **argv) {
  gtk_init(&argc, &argv);
  const char *mode = argc > 1 ? argv[1] : "render";
  if (strcmp(mode, "exit") == 0)
    return 0;
  GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
  gtk_window_set_title(GTK_WINDOW(window), "AppFlowy smoke fixture");
  gtk_window_set_default_size(GTK_WINDOW(window), 640, 480);
  if (strcmp(mode, "blank") != 0) {
    GtkWidget *label = gtk_label_new(NULL);
    gtk_label_set_markup(GTK_LABEL(label),
                         "<span size='30000'>AppFlowy\nRendered window</span>");
    gtk_container_add(GTK_CONTAINER(window), label);
  }
  if (strcmp(mode, "hidden") != 0)
    gtk_widget_show_all(window);
  if (strcmp(mode, "abort") == 0)
    g_timeout_add(1200, abort_after_drawing, NULL);
  gtk_main();
  return 0;
}
