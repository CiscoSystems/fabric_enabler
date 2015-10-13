/*******************************************************************************

  LLDP Agent Daemon (LLDPAD) Software
  Copyright(c) 2007-2012 Intel Corporation.

  This program is free software; you can redistribute it and/or modify it
  under the terms and conditions of the GNU General Public License,
  version 2, as published by the Free Software Foundation.

  This program is distributed in the hope it will be useful, but WITHOUT
  ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
  FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
  more details.

  You should have received a copy of the GNU General Public License along with
  this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin St - Fifth Floor, Boston, MA 02110-1301 USA.

  The full GNU General Public License is included in this distribution in
  the file called "COPYING".

  Contact Information:
  open-lldp Mailing List <lldp-devel@open-lldp.org>

*******************************************************************************/

#ifndef _LLDP_MAND_CMDS_H
#define _LLDP_MAND_CMDS_H

#define ARG_MAND_SUBTYPE "subtype"
#define ARG_TTL_VALUE "value"

#define TTL_MIN_VAL 0x0
#define TTL_MAX_VAL 0xFFFF

struct arg_handlers *mand_get_arg_handlers();

int mand_clif_cmd(void *data,
		  struct sockaddr_un *from,
		  socklen_t fromlen,
		  char *ibuf, int ilen,
		  char *rbuf, int rlen);

#endif
