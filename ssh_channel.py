import paramiko


class SSHChannel(object):

    def __init__(self, host, port, user, secret):
        self.host = host
        self.port = port
        self.user = user
        self.secret = secret
        self.channel = None
        self.client = None
        self.is_connected = False        
        
    def connect(self):
        assert not self.is_connected, "Already connected"
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        self.client.set_missing_host_key_policy(paramiko.WarningPolicy())
        self.client.connect(self.host, self.port, self.user, self.secret)
        self.is_connected = True
   
    def get_shell(self):
        assert self.is_connected, "Not connected yet"
        self.channel = self.client.invoke_shell()
        return self.channel

    def exec_remote(self, cmd):
        assert self.is_connected, "Not connected yet"
        (stdin, stdout, stderr) = self.client.exec_command(cmd)
        stdin.close()
        out_lines = []
        for line in stdout.readlines():
            out_lines.append(line[:-1])
        err_lines = []
        for line in stderr.readlines():
            err_lines.append(line[:-1])
        
        return (out_lines, err_lines)

    def close(self):
        assert self.is_connected, "Not connected yet"
        self.client.close()

