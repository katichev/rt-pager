import json
import logging
import threading
import socket
import select
import uuid
import time
from plugs import PlugLess, PlugLs
from ssh_channel import SSHChannel

SESSION_TIMEOUT = 300
BUFF_SIZE = 512
OUT_BUFF_SIZE = 512 # TODO check maximum allowed chunk size for nonblocking write
logger = logging.getLogger('%s'%(__name__))
logger.addHandler(logging.NullHandler())

class WebClient(threading.Thread):
    PL_ACTIVE = True
    PL_IDLE = False
    
    def __init__(self, sock, addr):
        """
            @param sock - client's socket object
            @param addr - client's addr, used by logger
        """
        threading.Thread.__init__(self)
        self.name = '['+str(addr)+']: '
        self.sock = sock
        self._buff = ''
        self._out_buff = []
        self._sock_write_fd = []
        self._sock_read_fd = [self.sock]
        self.running = True
        self._sessions = {}     # key: ssh connection uuid; value: list [ssh connection, last timestamp]
        self._log_sessions = {} # key: logfile uuid; value: list [plug instance, is_active, current_command, conn_id]


    def recv_from_client(self, data):
        self._buff = self._buff + str(data)
        if '\r\n' in self._buff:
            raw_cmd, self._buff = self._buff.split('\r\n', 1)
        else:
            if  len(self._buff)>1024:
                logger.warning(self.name+'Input buffer overrun, longer than 1024 bytes: %s'%self._buff)
                self._buff = ''
            return
        logger.debug(self.name+"some data has arrived: " + raw_cmd)
        try:
            req = json.loads(raw_cmd)
        except (ValueError, TypeError) as e:
            logger.warning(self.name+"Not a JSON! Ignoring...")
            return

        if req.get('cmd', None) == None:
            logger.warning(self.name+"there is no 'cmd' field! Ignoring...")
            return

        cmd = req['cmd']
        logger.info(self.name+"cmd = "+cmd)
        conn_id = req.get('conn_id', None) 
        log_id = req.get('log_id', None)
        
        if cmd == 'connect':
            self._connect(**req)
            return

        elif self._is_valid(conn_id=conn_id):
            if cmd == 'log_open':
                self._log_open(**req)
                return
            elif cmd == 'get_dir':
                self._get_dir(**req)
                return
            elif cmd == 'close':
                self._disconnect(conn_id)

        elif self._is_valid(log_id=log_id):
            if cmd in ['log_page', 'log_next', 'log_prev', 'log_pos', 'log_close']:
                self._log_cmd(**req)
                return

        logger.warning(self.name+"unable to excecute command: " + req['cmd'])
            
    def run(self):
        while self.running:
            reads,w,x = select.select(self._sock_read_fd, self._sock_write_fd,
                                      self._sock_read_fd ,0.5)
            for read_obj in reads:
                if read_obj==self.sock:
                    data = self.sock.recv(BUFF_SIZE)
                    if not data:
                        logger.info(self.name+"Client disconnect")
                        self._client_disconnect()
                        return
                    self.recv_from_client(data)            
                else:
                    if read_obj.check_response():
                        log_id = read_obj.__log_id
                        if self._log_sessions[log_id][1]:
                            self._log_response(log_id)
            if w:
                self.send_to_client()

            for ex_obj in x:
                if ex_obj==self.sock:
                    logger.error(self.name+ "Client's connection error")
                    self._client_disconnect()
                    return
                else:
                    logger.error(self.name+ "Log channel error, log_id=%s"%ex_obj.__log_id)
                    self._disconnect_log(ex_obj.__log_id)

            self._pool_expired()

    def _pool_expired(self):
            expired = []
            for log_id, (log, status, cmd, conn) in self._log_sessions.iteritems():
                if log.channel.exit_status_ready():
                    logger.warning(self.name+"Log channel has been unexpectedly closed,\
                                                 log_id=%s"%log_id)
                    expired.append(log_id)
            for l in expired:
                self._disconnect_log(l)

            expired = []
            for conn_id, (conn, prev_time) in self._sessions.iteritems(): 
                if prev_time + SESSION_TIMEOUT < time.time():
                    logger.warning(self.name+"SSH session has expired,\
                                                 conn_id=%s"%conn_id)
                    expired.append(conn_id)

            for c in expired:
                self._disconnect(c)

            
                    
    def stop(self):
        self.running = False

    def _client_disconnect(self):
        logger.info(self.name+'Terminating')
        self.running = False
        self.sock.close()
        rm_list = self._sessions.keys()
        for rm_id in rm_list:
            self._disconnect(rm_id)        
        

    def _get_dir(self, **kwargs):
        path = kwargs.get('path', '')
        conn_id = kwargs['conn_id']
        
        conn = self._touch_conn(conn_id)
        kwargs['ssh'] = conn

        try:
            ls_exec = PlugLs(**kwargs)
        except Exception as e:
            logger.warning(self.name+'Unable to run ls: %s'%str(e))
            res = {'cmd':kwargs['cmd'], 'res':'error', 'data':str(e)}
        else:
            ls_exec.put_request(path)
            ls_exec.check_response()
            (out, err) = ls_exec.get_result()
            if err==[]:
                ex_res = 'ok'
                data = out
            else:
                ex_res = 'err'
                data = err
            res = {'cmd':kwargs['cmd'], 'res':ex_res, 'data':data}
        self._put_answer_in_queue(res)

    def _connect(self, **kwargs):
        host = kwargs.get('host', None)
        
        port = kwargs.get('port', 22)
        user = kwargs.get('user', None)
        secret = kwargs.get('secret',None)
        try:
            ssh_conn = SSHChannel(host=host, port=port, user=user, secret=secret)
            ssh_conn.connect()
        except Exception as e:
            logger.warning('Unable to start ssh session: %s'%str(e))
            res = {'cmd':kwargs['cmd'], 'res':'error'}
        else:
            conn_id = str(uuid.uuid4())
            self._sessions[conn_id] = [ssh_conn, time.time()]
            logger.info(self.name+'New ssh session was registered, conn_id = %s' % conn_id)
            res = {'cmd':kwargs['cmd'], 'res':'ok', 'conn_id':conn_id}
        
        self._put_answer_in_queue(res)

    def _log_open(self, **kwargs):
        conn_id = kwargs['conn_id']
        conn = self._touch_conn(conn_id)
        kwargs['ssh'] = conn
        log = PlugLess(**kwargs)
        
        log_id = str(uuid.uuid4())
        log.__log_id = log_id
        self._log_sessions[log_id] = [log, self.PL_ACTIVE, kwargs['cmd'], conn_id]
        self._sock_read_fd.append(log)
        logger.info(self.name+'New log was registered, log_id = %s' % log_id)
        log.put_request(PlugLess.OPEN)
        
    def _log_cmd(self, **kwargs):
        log_id = kwargs['log_id']
        cmd = kwargs['cmd']

        log_cmd = None
        log_arg = None
        if cmd=='log_page':
            log_cmd = PlugLess.REDRAW
        elif cmd=='log_next':
            log_cmd = PlugLess.FWD
        elif cmd=='log_prev':
            log_cmd = PlugLess.BACK
        elif cmd=='log_pos':
            log_cmd = PlugLess.POS
            log_arg = kwargs.get('position', 0)
        elif cmd=='log_close':
            self._disconnect_log(log_id)
            res = {'cmd':cmd, 'res':'ok', 'log_id':log_id}
            self._put_answer_in_queue(res)
            return
        else:
            return

        log = self._touch_log(log_id, self.PL_ACTIVE, cmd=cmd)
        try:
            if log_cmd:
                log.put_request(log_cmd, log_arg)
        except:
            self._touch_log(log_id) # reset state
            res = {'cmd': cmd, 'res':'error', 'log_id':log_id}
            self._put_answer_in_queue(res)


    def _touch_log(self, log_id, state=PL_IDLE, cmd=None):
        """ Updates log state and touches associated ssh connection """
        log = self._log_sessions[log_id][0]
        conn_id = self._log_sessions[log_id][3]
        self._log_sessions[log_id] = [log, state, cmd, conn_id]
        self._touch_conn(conn_id)
        return log

    def _touch_conn(self, conn_id):
        """ 
            Update timestamp of SSH channel 
            @return ssh channel
        """
        conn = self._sessions[conn_id][0]
        self._sessions[conn_id] = [conn, time.time()]
        return conn

    def _is_valid(self, conn_id=None, log_id=None):
        if conn_id:
            return conn_id in self._sessions.keys()
        if log_id:
            return log_id in self._log_sessions.keys()

        return False
        

    
    def _disconnect(self, conn_id):
        logger.info(self.name+"Going to close conn_id = %s"%conn_id)
        logs_to_close=[]
        for k,v in self._log_sessions.iteritems():
            if v[3]==conn_id:
                logs_to_close.append(k)

        for l in logs_to_close:
            self._disconnect_log(l)

        self._sessions[conn_id][0].close()
        del self._sessions[conn_id]    
        logger.info(self.name+"conn_id = %s is no longer available"%conn_id) 

    def _disconnect_log(self, log_id):
        logger.info(self.name+"Closing log_id = %s"%log_id)
        log = self._log_sessions[log_id][0]
        try:
            log.close()
        except:
            pass
            
        if log in self._sock_read_fd:
            self._sock_read_fd.remove(log)
        del self._log_sessions[log_id]

    def _log_response(self, log_id):
        log_data = self._log_sessions[log_id][0].get_result()
        logger.info(self.name + 'Log is ready, log_id = %s'%log_id)
        res = {'cmd': self._log_sessions[log_id][2], 'res':'ok', 'log_id':log_id, 'data':log_data}
         
        self._put_answer_in_queue(res)
        self._touch_log(log_id)
    
    def _put_answer_in_queue(self, data):
        ''' Puts data into output (client's) queue
            @param data: dict with data
        '''
        logger.info('Going to put answer data in queue')
        enc = json.dumps(data)+'\r\n'
        enc_len = len(enc)
        for pos in range(0, enc_len, OUT_BUFF_SIZE):
            self._out_buff.append(enc[pos:pos+OUT_BUFF_SIZE])
        self._sock_write_fd = [self.sock]

    def send_to_client(self):
        if self._out_buff:
            data = self._out_buff.pop(0)
            self.sock.send(data)
        else:
            self._sock_write_fd = []
    

def main():
    import logging.config
    logging.config.fileConfig('log.conf')
    global logger
    # create logger
    logger = logging.getLogger('main')

    host = '127.0.0.1'
    port = 9999
    print 'listening %s:%s'%(host,port)
    print('press ctrl-c to exit...')
    backlog = 5
    size = 1024
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host,port))
    s.listen(backlog)
    conn_list = []
    while 1:
        try:
            client, address = s.accept()     
            wc = WebClient(client, address)
            wc.start()
            conn_list.append(wc)
        except:
            [t.stop() for t in conn_list]
            break

    [t.join() for t in conn_list]

if __name__=="__main__":
    main()
