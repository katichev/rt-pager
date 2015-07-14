import os
import StringIO
import logging
import re
from ssh_channel import SSHChannel

_ESC_POSITIVE = b'\x1b[m'
_ESC_ERASE_RIGHT = b'\x1b[K'
_ESC_RETURN_BUFFER = b'\x1b[?1049l'

logger = logging.getLogger('lib.%s'%(__name__))
logger.addHandler(logging.NullHandler())

class PlugLessException(Exception):
    pass

def repr_unprint(s):
    out=[]
    for ch in str(s):
        if ord(ch)>31:
            out.append(ch)
        else:
            out.append("\\x%02x"%ord(ch))
    return "".join(out)

class PlugGeneric:
    def __init__(self, ssh=None, **kwargs):
        if not ssh:
            self._ssh_owner = True
            host = kwargs.get('host',None)
            port = kwargs.get('port',22)
            user = kwargs.get('user', '')
            secret = kwargs.get('secret', '')
            
            self.ssh = SSHChannel(host=host, port=port, user=user, secret=secret)
            self.ssh.connect()
        else:
            self._ssh_owner = False
            self.ssh = ssh

        self.shell = False
        self.channel = None

    def start_shell(self, cols=80, rows=24):
        if not self.shell:
            self.channel = self.ssh.get_shell()
            if cols!=80 or rows!=24:
                self.channel.resize_pty(width=cols, height=rows)

    def close(self):
        if self._ssh_owner:
            self.ssh.close()       
        else:
            self.channel.close() 

    def flush(self):
        if self.shell:
            while self.channel.recv_ready():
                self.channel.recv(256)

    def fileno(self):
        return self.channel.fileno()

    def check_response(self):
        """ Used to process Plug data received from remote side. 
            Must be overriden in child

            @return True if there are some result
            @return False - no data available
        """
        assert False, "Override!"

    def get_result(self):
        """ Used to read result of last command.

            @return data
        """
        assert False, "Override!"

    def put_request(self):
        """ Used to feed command to remote side.

            May raise exception if the request cannot be completed.
        """
        assert False, "Override!"

    
class ScreenBuff(object):
    def __init__(self, cols = 80, rows = 24):
        self.cols = cols
        self.rows = rows
        self.posx = 1
        self.posy = 1
        self.anchor = []
        self.last_anchor = None
        self.a_len = 0
        self._buff = [StringIO.StringIO() for row in range(self.rows)]
        self._wrap = [False for r in range(self.rows)]
        self._ESC_mode = None
        self.line_counter = 0 
        self._skip_prompt = False

    def wait_new_anchor(self, anchors):
        assert anchors
        for a in anchors:
            if len(a):
                self.anchor.append((a,0,len(a),))
    
    def _new_line(self, reverse=False, wrap=False):
        if not reverse:
            #if self.line_counter < self.rows-1:
            self.line_counter = self.line_counter + 1
            if self.posy==self.rows:
                self._buff.pop(0)
                self._buff.append(StringIO.StringIO())
                self._wrap[self.posy-1] = wrap
                self._wrap.pop(0)
                self._wrap.append(False)
            else:
                self._wrap[self.posy-1] = wrap
                self.posy = self.posy + 1
        else:
            if self.posy==1:
                self._buff.pop()
                self._buff.insert(0,StringIO.StringIO())
                self._wrap.pop()
                self._wrap.insert(0,False)
            else:
                self.posy = self.posy - 1

    def _trunc_end_line(self, pos = 1):
        self._buff[self.posy-1].seek(pos-1)
        self._buff[self.posy-1].truncate()

    def _safe_move(self, col, row):
        if col>0 and col<=self.cols:
            self.posx = col
        else:
            logger.warning("_safe_move: out_of_screen by x axis: %d"%col)
        if row>0 and row<=self.rows:
            self.posy = row
        else:
            logger.warning("_safe_move: out_of_screen by y axis: %d"%row)
    

    def _ESC_pinball(self, ch):
        """ Removes ESC sequence from input string.
            @return True if char should be removed from string
                    False if char is not a part of ESC sequence
        """
        if self._ESC_mode==None:
            if ch=='\x1b':                  # ESC!
                self._ESC_mode = 'U'
            else:
                return False
            
        elif self._ESC_mode=='U':
            if ch in '=>':                  # these are known to appear in 'less' output
                self._ESC_mode = None
            elif ch=='M':                   # move position line up
                self._new_line(reverse=True)
                self._ESC_mode = None    
            elif ch=='[':   # CSI           
                self._ESC_buff = ''
                self._ESC_mode = 'CSI'
             
            elif ch==']':   # OSC           # operating systems control
                self._ESC_mode = 'OSC'
                
            else:
                logger.warning("Unaccounted ESC sequence started as %s"%repr_unprint('ESC'+ch))
                self._ESC_mode = None
        
        elif self._ESC_mode=='CSI':
            
            if ord(ch)>95 and ord(ch)<127 or ord(ch)>63 and ord(ch)<91:  # means end of sequence
                if ch == 'K' and self._ESC_buff == '':      # clear everything to the right
                    self._trunc_end_line(self.posx)         
                
                elif ch == 'H':
                    # move to (y;x) default (1;1)
                    ret = re.search("(\d*);(\d*)", self._ESC_buff)
                    row,col = 1,1                
                    if ret:
                        (tr,tc) = ret.groups()
                        col = int(tc) if tc else 1
                        row = int(tr) if tr else 1
                    self._safe_move(col,row)
                
                else:
                    logger.warning("Unaccounted ESC CSI sequence ESC%s%s"%(self._ESC_buff,ch))    
                
                self._ESC_mode = None                

            else:
                self._ESC_buff = self._ESC_buff + ch
        
        elif self._ESC_mode=='OSC':
            # skip until last esc cmd char 
            if ch=='\x07':
                self._ESC_mode = None
            elif ch=='\x1b':    # stop sequence is 'ESC \' 
                self._ESC_mode = 'U'
        
        # true means 'skip this'
        return True
        
    def set_skip_prompt(self):
        self._skip_prompt = True

    def _put_char(self, ch):
        
        if self._ESC_pinball(ch):
            pass
            
        elif ch=='\b':
            if self.posx==1:
                logger.warning('backspace at pos "1"')
            else:
                self.posx = self.posx - 1
                # that's not correct! we have to remove single char!
                
                self._trunc_end_line(self.posx)
        elif ch=='\r':
            self.posx = 1
        elif ch=='\n':
            self._new_line()
        else:
            if self.posx > self.cols:
                self.posx = 2
                self._new_line(wrap=True)
            else:
                self.posx = self.posx + 1
            self._buff[self.posy-1].write(ch)

    def put_data(self, buff, anchor_only=False):
        """
            Used to process ASCII data returned by 'less' over ssh
            @param buff - data to process
            @param anchor_only - Used if you need to find anchors in input stream only
        """
        found = False
        for i,ch in enumerate(buff):
            
            #seeking for anchor
            updated = []
            for text, pos, lenh in self.anchor:
                if ch == text[pos]:
                    pos = pos+1
                    if pos == lenh:
                        if self._skip_prompt:
                            self._skip_prompt = False
                            pos = 0
                        else:
                            logger.info("Pattern '%s' was found"%repr_unprint(text))
                            self.last_anchor = text 
                            self.anchor = []
                            if i<len(buff)-1:
                                logger.warning("Pattern was found but buffer is not empty: %s"%repr_unprint(buff[i:]))
                            found = True
                            break
                else:
                    pos = 0        
                updated.append((text,pos,lenh,))
            if not anchor_only: self._put_char(ch)
            if found: 
                self._ESC_mode = None
                break
            self.anchor = updated

    def anchor_found(self):
        return self.anchor == []
        
    def curr_line(self):
        return self._buff[self.posy-1].getvalue()

    def __repr__(self):
        ''' Returns buffer representation. Ignores last line '''
        nl =  lambda row: '\n' if not self._wrap[row] else '' 
        text = [self._buff[row].getvalue() + nl(row)  for row in range(self.rows-1)]
        return ''.join(text)
    
class PlugLess(PlugGeneric):
    # cmd ::= ('cmd_name',('anchor1','anchor2',...))
    OPEN = ('open', (_ESC_POSITIVE+_ESC_ERASE_RIGHT, '(END) \x1b', 'No such file'))
    CLOSE = ('close', (_ESC_ERASE_RIGHT,''))
    FWD = ('fwd', (':'+_ESC_ERASE_RIGHT, '(END) \x1b'))
    REDRAW = ('redraw', (':'+_ESC_ERASE_RIGHT, '(END) \x1b'))
    BACK = ('back', (':'+_ESC_ERASE_RIGHT, '\x07\x0d\x1b'))
    POS = ('pos', (';1H\x0d\x1b[K:', '(END) \x1b', ':'+_ESC_ERASE_RIGHT))
    TASKS = [OPEN, CLOSE, FWD, BACK, POS, REDRAW]
            
    
    REDRAW_AFTER_BACK = True    # BACK or POS commands cause 'less' to draw screen upside down
                                # so it is impossible to determine line wrap
                                # Set the flag to 'True' to redraw screen after BACK or POS commands 

    def __init__(self, **kwargs):
        PlugGeneric.__init__(self, **kwargs)
        
        self.log_path = kwargs.get('path', '/var/log/dmesg')
        cols = kwargs.get('cols', 80)
        rows = kwargs.get('rows',24)        
        self.start_shell(cols,rows)

        self.has_task = False
        self.task = None
        self.screen_buff = ScreenBuff(cols, rows)
        self.launched = False
        self._first_screen = True
        self._last_screen = False
    
    def put_request(self, new_task, args=None):
        assert not self.has_task, "Unable to add a new request: in progress"
        assert new_task in self.TASKS, "Unknown new task %s"%new_task
        
        if self._first_screen and new_task==self.BACK or \
            self._last_screen and new_task==self.FWD:
            logger.error(self.name+"Cannot move beyond")
            raise PlugLessException("Cannot move beyond")      

        if not self.launched and new_task!=self.OPEN:
            raise PlugLessException("Open first!")


        logger.info("New task: '%s'", new_task[0])
        self.has_task = True
        self.task = new_task
        self.screen_buff.wait_new_anchor(new_task[1])
        self.screen_buff.line_counter = 0            
        if self.task == self.OPEN:
            self.cmd_open()
        elif self.task == self.CLOSE:
            self.cmd_close()
        elif self.task == self.FWD:
            self.cmd_fwd()
        elif self.task == self.BACK:
            self.cmd_back()
        elif self.task == self.POS:
            self.cmd_pos(args)
            self.screen_buff.set_skip_prompt()
        elif self.task == self.REDRAW:
            self.cmd_redraw()
        
    def check_response(self):
        """
            Process next chunk of data received through self.channel
            @return True if task was finished or if there is no tasks currently
                    False otherwise 
        """
        if self.channel.recv_ready():
            buff = self.channel.recv(256)
            logger.debug("new_data:\n"+repr_unprint(buff))
        else:    
            buff = ''
        
        if self.has_task and buff:        
            self.screen_buff.put_data(buff, anchor_only=(self.REDRAW_AFTER_BACK and self.task in [self.BACK, self.POS]))
            if self.screen_buff.anchor_found():

                if self.task in [self.FWD, self.POS]: self._first_screen = False
                if self.task in [self.BACK, self.POS]: self._last_screen = False
                if self.screen_buff.last_anchor == self.task[1][1]:
                    if self.task in [self.OPEN, self.FWD, self.POS]:
                        self._last_screen = True
                        logger.info("Last screen is reached")
                    if self.task==self.BACK:
                        self._first_screen = True
                        logger.info("First screen is reached")
                                            
                if self.task==self.OPEN:
                    if self.screen_buff.last_anchor != self.task[1][2]:
                        logger.info("File is open")
                        self.launched = True
                    else:
                        logger.warning("File was not found!")
                
                if self.REDRAW_AFTER_BACK:
                    if self.task in [self.BACK, self.POS]:
                        self.has_task = False
                        self.put_request(self.REDRAW)
                        return False

                self.has_task = False
                self.task = None
                logger.info("LINE counter = %d"%self.screen_buff.line_counter)
                return True

        elif not self.has_task:
            return True
        
        return False
        
    def get_result(self):
        if self.has_task:
            logger.error("Trying to read while task is not completed")
        return repr(self.screen_buff)
    
    def cmd_open(self):
        self.flush()
        logger.info("launching 'less %s'"%self.log_path)
        cmd_line = 'less '+self.log_path + '\n' 
        self.channel.send(cmd_line)

    def cmd_fwd(self):
        self.flush()
        logger.info("going forward")
        self.channel.send('f')

    def cmd_redraw(self):
        self.flush()
        logger.info("redraw")
        self.channel.send('r')

    def cmd_back(self):
        self.flush()
        logger.info("going back")
        self.channel.send('b')

    def cmd_close(self):
        self.flush()
        logger.info("quitting less")
        self.channel.send('q')
        self.launched = False

    def cmd_pos(self, pos):
        self.flush()
        try:
            fl_pos = float(pos)
            assert fl_pos<=100 and fl_pos>=0
        except:
            logger.warning("wrong position to move: '%s', moving to 0%%"%pos)
            fl_pos = 0
        else:
            logger.info("moving to %f%%"%fl_pos)
        self.channel.send('%f%%'%fl_pos)

class PlugLs(PlugGeneric):
    def __init__(self, **kwargs):
        PlugGeneric.__init__(self, **kwargs)
        self.executed = False
        self.path = ''
        self.buff = []

    def check_response(self):
        if not self.executed:
            command = 'ls -1 -d --color=never '+ self.path
            (self.out, self.err)  = self.ssh.exec_remote(command)
            self.executed = True
        return True

    def get_result(self):
        return (self.out, self.err)

    def put_request(self, path):
        self.path = path
        self.executed = False

