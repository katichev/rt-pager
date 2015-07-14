from plugs import ScreenBuff, PlugLess, repr_unprint
import unittest, mock

class ScreenBuffTest(unittest.TestCase):
    def test_anchor_found(self):
        """Test if ScreenBuff can find anchors"""
        sb = ScreenBuff()
        cases = [(sb, "found",("abc",),"xyzabczyz"),
                 (sb, "found",("abc",),"abc"),
                 (sb, "found",("\x1b",),"\x1b"),
                 (sb, "found",("\x1b[?",),"xyz\x1b[?zyz"),
                 (sb, "found",("a\x1bbcdefgh",),"xyza\x1bbcdefghzyz"),
                 (sb, "not found",("abc",),"xyzabzyz"),
                 (sb, "not found",("aa x1b 23",),"aa \x1b 23"),
                 (sb, "found", ("aa","bb"), "ababb"),
                 (sb, "found", ("(END) \x1b","long"), "a (END) \x1b[K"),
                 ]
        print '\n'
        [self.check_anchor(tc) for tc in cases] 

    def test_empty_anchor(self):
        """Test claim about empty search"""
        sb = ScreenBuff()
        self.assertRaises(AssertionError, sb.wait_new_anchor, "")

    def check_anchor(self, tc):
        if tc[1]=="found":
            sb, anchor, buff = tc[0], tc[2], tc[3]
            print ("Seeking for '%s' in '%s'" % (repr_unprint(anchor), repr_unprint(buff)))
            sb.wait_new_anchor(anchor)
            sb.put_data(buff) 
            self.assertTrue(sb.anchor_found())
            
        if tc[1]=="not found":
            sb, anchor, buff = tc[0], tc[2], tc[3]
            print ("Seeking for '%s' in '%s'" % (anchor, buff))
            sb.wait_new_anchor(anchor)
            sb.put_data(buff)
            self.assertFalse(sb.anchor_found())

    def test_pos(self):
        """Test buffer position after we put symbols there"""
        cases = [(1,1,"1234567890",11,1),
                 (1,1,"123",4,1),
                 (1,10,"a",2,10),
                 (1,10,"1234567890",11,10),
                 (1,1,b"backspace! \x08", 1,2),
                 (1,1,b"backspace! \x08\r\n", 1,3),
                 (1,1,"\r\n",1,2),
                 (10,1,"\r\n",1,2),
                 (5,1,"\r",1,1),
                 (5,1,"\n",5,2),
                 (10,1,"a\r\nb",2,2),
                 (1,1,"abc\x1bM",4,1),
                 (1,3,"abc\x1bM",4,2),
                 (1,3,"abc\x1b[H",1,1),
                 (1,3,"abc\x1b[10;4H",4,10),
                 (1,10,"abc\r\x1b[K", 1,10),
                 (1,5, "a\x1bMbc",4,4),
                 (1,1, "a\x1bMbc",4,1)
                 ]
        print '\n'
        for c in cases:
            sb = ScreenBuff(10,10)
            self.check_pos(sb,c)

    def check_pos(self, sb, tc):
        sb.posx, sb.posy, buff, newx, newy = tc[0],tc[1],tc[2],tc[3],tc[4]
        buff_pr = buff.replace('\x08','\\b')
        buff_pr = buff_pr.replace("\n","\\n")
        buff_pr = buff_pr.replace("\r","\\r")
        buff_pr = buff_pr.replace("\x1b","ESC")
        print "x,y=(%d,%d) buf='%s', expect x,y=(%d,%d)"%(tc[0],
                               tc[1],buff_pr,tc[3],tc[4])
        sb.put_data(buff)
        self.assertEquals(newx, sb.posx)
        self.assertEquals(newy, sb.posy)
    
    def test_repr(self):
        """Test repr() for ScreenBuff"""
        cases = [(1,1,"a","a\n\n\n\n"),
                 (1,1,"1234567890","1234567890\n\n\n\n"), # 
                 (1,1,"1234567890 \x08ab","1234567890ab\n\n\n"),
                 (1,1,"\x1b=","\n\n\n\n"),
                 (1,1,"a\r\na\r\na\r\na\r\na","a\na\na\na\n"),
                 (1,1,"0123456789\r\nb","0123456789\nb\n\n\n"),
                 (1,1,"abc\x1bM","\nabc\n\n\n"),
                 (1,1,"abcde\x1b[1;2H\x1b[K","a\n\n\n\n"),
                 (1,5,"abc\r\x1b[K","\n\n\n\n")
                ]
        print '\n'
        for c in cases:
            sb = ScreenBuff(10,5)
            self.check_repr(sb,c)
    
    def check_repr(self, sb, tc):
        sb.posx, sb.posy, buff, expect = tc[0],tc[1],tc[2],tc[3]
        buff_pr = buff.replace('\x08','\\b')
        buff_pr = buff_pr.replace("\n","\\n")
        buff_pr = buff_pr.replace("\r","\\r")
        buff_pr = buff_pr.replace("\x1b","ESC")
        buff_o = expect.replace('\x08','\\b')
        buff_o = buff_o.replace("\n","\\n")
        buff_o = buff_o.replace("\r","\\r")
        buff_o = buff_o.replace("\x1b","ESC")
        print "x,y=(%d,%d) buf='%s', expect buff='%s'"%(tc[0],
                               tc[1],buff_pr,buff_o)
        sb.put_data(buff)
        out = repr(sb)
        self.assertEquals(expect, out)

    def test_wrap_last_line(self):
        """Test if screenbuff wraps last row correctly"""
        sb = ScreenBuff(10,5)
        sb.posx = 1
        sb.posy = 5
        sb._wrap = [True, False, False, True, False]
        buff = "0123456789a"
        sb.put_data(buff)
        self.assertItemsEqual(sb._wrap, [False, False, True, True, False])  

    def test_ESC(self):
        """Test if ScreenBuff ignores ESC sequences"""
        fin = '\n\n\n\n'
        cases = [("a\x1b=b","ab"+fin),
                 ("a\x1b[?1049l", "a"+fin),
                ]
        for c in cases:
            sb = ScreenBuff(10,5)
            self.check_repr(sb,tuple([1,1]+list(c)))

class PlugLessTest(unittest.TestCase):
    @mock.patch('plugs.SSHChannel')
    @mock.patch.object(PlugLess, 'flush') 
    def test_open(self, flush_mock, ssh_mock):
        channel = ssh_mock.return_value.get_shell.return_value
        channel.recv_ready.side_effect= [True, False]   # 1st for actual data
        channel.recv.return_value = ("xyz\r\n"+'(END)'+PlugLess.OPEN[1][0])
        pl = PlugLess(path='path', cols=5, rows=5)
        pl.put_request(PlugLess.OPEN)
        
        self.assertTrue(pl.check_response())
        channel.send.assert_called_with('less path\n')
        self.assertTrue(pl.launched)
        # call again without data available
        self.assertTrue(pl.check_response())

    @mock.patch('plugs.SSHChannel')
    @mock.patch.object(PlugLess, 'flush')
    def test_not_found(self, flush_mock, ssh_mock):
        channel = ssh_mock.return_value.get_shell.return_value
        channel.recv_ready.return_value = [True]
        channel.recv.return_value = ("aaa: No such file or directory\r\n")
        pl = PlugLess(path='path', cols=5, rows=5)
        pl.put_request(PlugLess.OPEN)

        self.assertTrue(pl.check_response())
        self.assertFalse(pl.launched)
        channel.send.assert_called_with('less path\n')

    
if __name__=="__main__":
    unittest.main()
