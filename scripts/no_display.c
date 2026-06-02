/* LD_PRELOAD stub: silences cvShowImage / cvWaitKey in headless conda envs
 * where the conda-forge OpenCV highgui has no GTK plugin compiled in.
 * Compile: gcc -shared -fPIC -o scripts/no_display.so scripts/no_display.c
 */
void cvShowImage(const char *name, const void *image) {}
int  cvWaitKey(int delay) { return -1; }
void cvDestroyWindow(const char *name) {}
void cvDestroyAllWindows(void) {}
