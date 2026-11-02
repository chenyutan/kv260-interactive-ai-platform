/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef PAL_PORT_H
#define PAL_PORT_H

/* Parse before SDL/config initialization. 0 continues, 1 is a clean early exit. */
int PAL_PortParse(int argc, char **argv);
int PAL_PortPrepare(void);
int PAL_PortSelfTestRequested(void);
int PAL_PortSelfTest(void);
int PAL_PortAudioEnabled(void);
const char *PAL_PortConfigDir(void);

#endif
