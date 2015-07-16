import web_client
import unittest
from mock import Mock, patch, PropertyMock



class WebClientTest(unittest.TestCase):
    
    def setUp(self):
        self.sock = Mock()
        self.wc = web_client.WebClient(self.sock, 'AnyName')
    
    @patch('web_client.logger')   
    def test_recv_from_client_invalid(self, logger):
        """test if recv_from_client ignores non-json"""
        data = 'ie23\r\n'
        self.wc.recv_from_client(data)
        logger.warning.assert_called_with('[AnyName]: Not a JSON! Ignoring...')
        self.assertFalse(logger.info.called)
    
    @patch.object(web_client.WebClient, '_connect')    
    @patch('web_client.logger')
    def test_recv_from_client_split(self, logger, conn):
        """test if recv_from_client accept splitted json; cmd = connect"""
        self.wc.recv_from_client('{"cmd":"c')
        self.wc.recv_from_client('onnect","param":"abc"}\r\n')
        logger.info.assert_called_with('[AnyName]: cmd = connect')
        conn.assert_called_with(cmd='connect', param='abc')
    
    @patch('time.time')
    @patch('uuid.uuid4')
    @patch.object(web_client.WebClient, '_put_answer_in_queue')
    @patch('web_client.SSHChannel')
    def test_connect(self, ssh, put_ans, m_uuid, m_time):
        """test if _connect() tries to establish ssh session and returns correct result"""
        args = {"host":"abc", "port":666, 
                   "user":"devil", "secret":"hell"}
        m_time.return_value=1234
        ssh_conn = ssh.return_value
        m_uuid.return_value='abc-def'
        #positive
        res = {"cmd":"any", "res":"ok", "conn_id":'abc-def'}
        self.wc._connect(cmd = 'any', **args)
        ssh.assert_called_with(**args)
        put_ans.assert_called_with(res)
        self.assertEqual(self.wc._sessions['abc-def'], [ssh_conn, 1234]) 
        
        #negative
        ssh.reset_mock()
        m_uuid.reset_mock()
        ssh.side_effect = Exception()
        res = {"cmd":"any", "res":"error"}
        self.wc._connect(cmd = 'any', **args)
        ssh.assert_called_with(**args)
        self.assertItemsEqual(m_uuid.call_args_list, [])
        put_ans.assert_called_with(res)        
    
    @patch('time.time')
    @patch('uuid.uuid4')
    @patch('web_client.PlugLess')
    def test_log_open(self, m_plug, m_uuid, m_time):
        """ create new log session on log_open call """
        conn = Mock()
        log = m_plug.return_value
        args = {'conn_id':'abc-def', 'cmd':'cmd','some_arg':'some_val'}
        self.wc._sessions = {'abc-def':[conn, 100]}
        m_uuid.return_value='aaa-bbb'
        m_time.return_value = 333
        self.wc._log_open(**args)
        m_plug.assert_called_with(ssh=conn, **args)
        self.assertEqual(self.wc._log_sessions['aaa-bbb'], 
                        [log, web_client.WebClient.PL_ACTIVE, 'cmd', 'abc-def'])
        self.assertEqual(self.wc._sessions['abc-def'], [conn, 333])                
        self.assertIn(log, self.wc._sock_read_fd)

    
    @patch.object(web_client.WebClient, '_log_open')
    @patch('web_client.logger')
    def test_log_open_called(self, logger, log_open):
        """log_open called only if correct conn_id provided"""
        self.wc._log_sessions['aaa-111']=[]
        self.wc.recv_from_client('{"cmd":"log_open","param":"aaa-111"}\r\n')
        self.assertItemsEqual(log_open.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"log_open","log_id":"aaa-111"}\r\n')
        self.assertItemsEqual(log_open.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"log_open","conn_id":"aaa-111"}\r\n')
        self.assertItemsEqual(log_open.call_args_list, []) 

        self.wc._sessions['aaa-112']=[]
        self.wc.recv_from_client('{"cmd":"log_open","param":"aaa-112"}\r\n')
        self.assertItemsEqual(log_open.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"log_open","log_id":"aaa-112"}\r\n')
        self.assertItemsEqual(log_open.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"log_open","conn_id":"aaa-112"}\r\n')
        log_open.assert_called_with(cmd='log_open', conn_id='aaa-112')

    def test_disconnect(self):
        """test if _disconnect() closes all log channels and ssh channel"""

        s1 = Mock()
        s2 = Mock()
        self.wc._sessions ={'aaa':[s1,1],
                            'bbb':[s2,2]}
        log = Mock()
        self.wc._log_sessions ={'000':[log,None,None, 'aaa'],
                                '001':[log,None,None, 'aaa'],
                                '002':[log,None,None, 'bbb'],
                                '003':[log,None,None, 'ccc'],}
        self.wc._disconnect('aaa')
        self.assertDictEqual(self.wc._sessions, {'bbb':[s2,2]})
        self.assertDictEqual(self.wc._log_sessions,
                   {'002':[log,None,None, 'bbb'],
                    '003':[log,None,None, 'ccc']})
        s1.close.assert_called_once_with()
        self.assertItemsEqual(s2.close.call_args_list, [])
        self.assertEqual(log.close.call_count,2)
    

    @patch.object(web_client.WebClient, '_disconnect')
    @patch('web_client.logger')
    def test_disconnect_called(self, logger, close):
        """_disconnect called only if correct conn_id provided"""
        self.wc._log_sessions['aaa-111']=[]
        self.wc.recv_from_client('{"cmd":"close","param":"aaa-111"}\r\n')
        self.assertItemsEqual(close.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"close","log_id":"aaa-111"}\r\n')
        self.assertItemsEqual(close.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"close","conn_id":"aaa-111"}\r\n')
        self.assertItemsEqual(close.call_args_list, [])

        self.wc._sessions['aaa-112']=[]
        self.wc.recv_from_client('{"cmd":"close","param":"aaa-112"}\r\n')
        self.assertItemsEqual(close.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"close","log_id":"aaa-112"}\r\n')
        self.assertItemsEqual(close.call_args_list, []) 
        self.wc.recv_from_client('{"cmd":"close","conn_id":"aaa-112"}\r\n')
        close.assert_called_with('aaa-112')

    @patch.object(web_client.WebClient, '_log_cmd')
    @patch('web_client.logger')
    def test_log_cmd_called(self, logger, log_cmd):
        """_log_cmd called only if correct log_id provided"""
        self.wc._sessions['aaa-111']=[]
        self.wc.recv_from_client('{"cmd":"log_page","param":"aaa-111"}\r\n')
        self.assertItemsEqual(log_cmd.call_args_list, [])
        self.wc.recv_from_client('{"cmd":"log_page","log_id":"aaa-111"}\r\n')
        self.assertItemsEqual(log_cmd.call_args_list, [])
        self.wc.recv_from_client('{"cmd":"log_page","conn_id":"aaa-111"}\r\n')
        self.assertItemsEqual(log_cmd.call_args_list, [])

        self.wc._log_sessions['aaa-112']=[]
        self.wc.recv_from_client('{"cmd":"log_page","param":"aaa-112"}\r\n')
        self.assertItemsEqual(log_cmd.call_args_list, [])
        self.wc.recv_from_client('{"cmd":"log_page","conn_id":"aaa-112"}\r\n')
        self.assertItemsEqual(log_cmd.call_args_list, [])
        self.wc.recv_from_client('{"cmd":"log_page","log_id":"aaa-112"}\r\n')
        log_cmd.assert_called_with(cmd='log_page',log_id='aaa-112')


    def test_put_answer_in_queue(self):
        self.assertEquals(self.wc._out_buff, [])
        self.assertEquals(self.wc._sock_write_fd,[])
        data = {'abc':123}
        self.wc._put_answer_in_queue(data)
        self.assertEquals(self.wc._sock_write_fd,[self.wc.sock])
        self.assertEquals(self.wc._out_buff, ['{"abc": 123}\r\n'])

    @patch.object(web_client, 'OUT_BUFF_SIZE')
    def test_put_answer_in_queue_long(self, buff):
        web_client.OUT_BUFF_SIZE = 4 # mock is used here to correctly restore constant after all
        self.assertEquals(self.wc._out_buff, [])
        self.assertEquals(self.wc._sock_write_fd,[])
        data = {'abc':123}
        self.wc._put_answer_in_queue(data)
        self.assertEquals(self.wc._sock_write_fd, [self.wc.sock])
        self.assertEquals(self.wc._out_buff, ['{"ab', 'c": ', '123}', '\r\n'])
    
    @patch.object(web_client.WebClient, '_touch_conn')
    @patch.object(web_client.WebClient, '_put_answer_in_queue')
    def test_log_response(self, put_mock, m):
        plug = Mock()
        plug.get_result.return_value('okay\r\nokay\r\n')
        self.wc._log_sessions['123-xyz'] = [plug, self.wc.PL_ACTIVE, 'open_log', 777]
        self.wc._log_response('123-xyz')
        
        self.assertItemsEqual(self.wc._log_sessions, {'123-xyz':[plug, self.wc.PL_IDLE, None, 777] })
        put_mock.assert_called_with({'cmd':'open_log','res':'ok', 'data':plug.get_result.return_value, 'log_id':'123-xyz'})

    @patch.object(web_client.WebClient, '_client_disconnect')
    @patch.object(web_client,'SESSION_TIMEOUT')
    @patch('time.time')
    @patch('select.select')
    def test_run_session_expired(self, select_mock, time_mock, to_mock, cd_mock):
        select_s = [[[],[],[]] for x in xrange(8)]
        select_s.append([[self.sock],[],[]])    # this will cause stop at 9th lap
        select_mock.side_effect = select_s
        self.sock.recv.return_value = ''

        web_client.SESSION_TIMEOUT = 30
        time_mock.side_effect = [5*x for x in range(16)]   # timeput will expire (becomes > 30) at 4th lap
        
        ssh_ch_mock = Mock()
        ssh_not_exp_mock = Mock()
        self.wc._sessions= {'666-xxx':(ssh_ch_mock, 0),
                            '777-yyy':(ssh_not_exp_mock, 999)}  # will not expire

        # cross fingers and hope it won't freeze
        self.wc.run()
    
        # normally this should be empty, but here we have overriden _client_disconnect
        self.assertEquals(self.wc._sessions, {'777-yyy':(ssh_not_exp_mock, 999)}) 
        
        ssh_ch_mock.close.assert_called_with()
        cd_mock.assert_called_with()

if __name__=='__main__':
    unittest.main()
