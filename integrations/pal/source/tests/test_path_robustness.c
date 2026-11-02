/* Compile with -ffunction-sections -fdata-sections -Wl,--gc-sections,
 * -Iinclude and SDL 1.2 header flags. No game data or SDL initialization needed. */
#include <unistd.h>
#include "../src/misc/util.c"
int main(void)
{
   char path[1024], temp[] = "./path-fixture-XXXXXX";
   int fd = mkstemp(temp); assert(fd >= 0); close(fd);
   assert(myAccess(temp, 0) == 0);
   assert(UTIL_GetFullPathName(path, sizeof(path), ".", temp) != NULL);
   assert(remove(temp) == 0);
   for (int i = 0; i < 10; i++)
   {
      assert(myAccess(temp, 0) == -1);
      assert(UTIL_GetFullPathName(path, sizeof(path), ".", temp) == NULL);
   }
   puts("PASS path: existing and missing files, repeated safe failure");
   return 0;
}
