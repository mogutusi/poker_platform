import binascii
import random
from . import sm3
import pickle
import os
from .sm4 import sm4_cbc_enc,sm4_cbc_dec


sm2_table = {
    'n': 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123,
    'p': 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF,
    'gx': 0x32c4ae2c1f1981195f9904466a39c9948fe30bbff2660be1715a4589334c74c7,
    'gy':  0xbc3736a2f4f6779c59bdcee36b692153d0a9877cc62a474002df32e52139f0a0,
    'a': 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC,
    'b': 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93,
}

def on_curve(x,y):
    n = sm2_table['p']
    x = x %n
    y = y %n
    l = (y*y)%n
    r = ((x*x*x)%n + sm2_table['a']*x +sm2_table['b'])%n
    return l==r


def to_byte(x, size=None):
    if size is None:  # 计算合适的字节数
        size, tmp = 0, x >> 64
        while tmp:
            size += 8
            tmp >>= 64
        tmp = x >> (size << 3)
        while tmp:
            size += 1
            tmp >>= 8
    elif x >> (size << 3):  # 指定的字节数不够则截取低位
        x &= (1 << (size << 3)) - 1
    return x.to_bytes(size, byteorder='big')

def bytesjoin(*list):
    b = b''
    for i in list:
        b+=i
    return b


class sm2():

    def __init__(self,private_key:int,id:str,ENTL=None):
        self.p= sm2_table['p']
        self.a= sm2_table['a']
        self.b = sm2_table['b']
        self.gx=sm2_table['gx']
        self.gy = sm2_table['gy']
        self.n = sm2_table['n']
        self.private_key=private_key%self.n
        self._read_prekg()
        self._get_public_key()
        self.id =bytes(id, encoding = 'utf-8')  # id 为 字符串
        self.invs_prk = ModInverse(1+self.private_key,self.n)
        self.ENTL=len(id)<<3
        self.Za=sm3.sm3_hash(bytesjoin(to_byte(self.ENTL,2),self.id,b'\xff\xff\xff\xfe\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff\xff\xff\xff\xfc',b'(\xe9\xfa\x9e\x9d\x9f^4MZ\x9eK\xcfe\t\xa7\xf3\x97\x89\xf5\x15\xab\x8f\x92\xdd\xbc\xbdAM\x94\x0e\x93',b'2\xc4\xae,\x1f\x19\x81\x19_\x99\x04Fj9\xc9\x94\x8f\xe3\x0b\xbf\xf2f\x0b\xe1qZE\x893Lt\xc7',b'\xbc76\xa2\xf4\xf6w\x9cY\xbd\xce\xe3ki!S\xd0\xa9\x87|\xc6*G@\x02\xdf2\xe5!9\xf0\xa0',to_byte(self.public_key[0],32),to_byte(self.public_key[1],32)))


    def _read_prekg(self):
        script_dir = os.path.dirname(__file__)
        prekg_path = os.path.join(script_dir, 'prekg.dat')
        with open(prekg_path,'rb') as f:
            gm = pickle.load(f)
            if gm !="gm_ttxs_sm2_prekg":
                raise ("错误预计算文件位置")
            else:
                self.kgpre = pickle.load(f)

    def _get_public_key(self):
        x,y=self._kg(self.private_key)
        self.public_key=(x,y)

    def _wnaf(self,w : int, N: int):
        wp = 1 << w
        wand = wp - 1
        whalf = 1 << (w - 1)
        naf = []
        i = 0
        while (N > 0):
            if N & 1 == 1:
                temp = N & wand
                if temp > whalf:
                    naf.append(temp - wp)
                else:
                    naf.append(temp)
                N = N - naf[i]
            else:
                naf.append(0)
            N = N >> (1)
            i = i + 1
        return reversed(naf)

    def _tP(self,t:int,pkx:int,pky:int):
        pkpre = {}
        xp = pkx
        yp = pky
        zp = 1
        xs_p, ys_p, zs_p = self._double_point(xp, yp, zp)
        xn = pkx
        yn = -pky
        zn = 1
        xs_n, ys_n, zs_n = self._double_point(xn, yn, zn)
        pkpre[1] = (xp, yp)
        pkpre[-1] = (xn, yn)
        for i in range(3, 8, 2):
            xp, yp, zp = self._point_add(xp, yp, zp, xs_p, ys_p, zs_p)
            xn, yn, zn = self._point_add(xn, yn, zn, xs_n, ys_n, zs_n)
            pkpre[i] = (self._jacb_to_nor(xp, yp, zp))
            pkpre[-i] = (self._jacb_to_nor(xn, yn, zn))
        k4naf = (self._wnaf(4, t))

        x2, y2, z2 = 1, 1, 0
        for i in k4naf:
            x2, y2, z2 = self._double_point(x2, y2, z2)
            if i != 0:
                x2, y2, z2 = self._point_add(x2, y2, z2, pkpre[i][0], pkpre[i][1], 1)
        return self._jacb_to_nor(x2,y2,z2)

    def _pre_kg(self):
        with open('prekg.dat', 'wb') as f:
            pickle.dump("gm_ttxs_sm2_prekg", f)
            kgpre = []
            x = self.gx
            y = self.gy
            z = 1
            for i in range(32):
                l = []
                l.append(self._jacb_to_nor(x, y, z))
                xp, yp, zp = self._double_point(x, y, z)
                for j in range(254):
                    l.append(self._jacb_to_nor(xp, yp, zp))
                    xp, yp, zp = self._point_add(xp, yp, zp, x, y, z)
                x, y, z = xp, yp, zp
                kgpre.append(l)
            pickle.dump(kgpre, f)

    def _kg(self,k):
        kt = k
        x, y, z = 1, 1, 0
        for i in range(32):
            t = kt & 0xff
            if t != 0:
                x, y, z = self._point_add(x, y, z, self.kgpre[i][t - 1][0], self.kgpre[i][t - 1][1], 1)
            kt = kt >> 8
        return self._jacb_to_nor(x, y, z)


    def sign(self,msg:bytes|str):
        if isinstance(msg, bytes):
            m = self.Za + binascii.b2a_hex(msg).decode()
        else:
            m = self.Za +binascii.b2a_hex(msg.encode()).decode()
        e = int(sm3.sm3_hash(binascii.a2b_hex(m.encode())),16)

        #randomk=0x59276E27D506861A16680F3AD9C02DCCEF3CC1FA3CDBE4CE6D54B80DEAC1BC21
        randomk = random.randint(1, self.n - 1)
        x1=self._kg(randomk)[0]

        r = (e+x1) % self.n
        while((r==0) or (r+randomk== self.n )):
            randomk = random.randint(1, self.n - 1)
            x1 = self._kg(randomk)[0]
            r = (e + x1) % self.n
        s = (self.invs_prk * (randomk - (r*self.private_key)%self.n))%self.n
        while(s==0):
            randomk = random.randint(1, self.n - 1)
            x1 = self._kg(randomk)[0]
            r = (e + x1) % self.n
            s = (self.invs_prk * (randomk - (r * self.private_key) % self.n)) % self.n
        return r,s


    def verify(self,r,s,msg,pkx,pky,id):
        if  ((0<r<self.n) and (0<s<self.n)) is False:
            return False
        t = (r + s) % self.n
        if t==0:
            return False
        ENTL = len(id)<<3
        id = bytes(id, encoding='utf-8')
        Za=sm3.sm3_hash(bytesjoin(to_byte(ENTL,2),id,b'\xff\xff\xff\xfe\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff\xff\xff\xff\xfc',b'(\xe9\xfa\x9e\x9d\x9f^4MZ\x9eK\xcfe\t\xa7\xf3\x97\x89\xf5\x15\xab\x8f\x92\xdd\xbc\xbdAM\x94\x0e\x93',b'2\xc4\xae,\x1f\x19\x81\x19_\x99\x04Fj9\xc9\x94\x8f\xe3\x0b\xbf\xf2f\x0b\xe1qZE\x893Lt\xc7',b'\xbc76\xa2\xf4\xf6w\x9cY\xbd\xce\xe3ki!S\xd0\xa9\x87|\xc6*G@\x02\xdf2\xe5!9\xf0\xa0',to_byte(pkx,32),to_byte(pky,32)))
        if isinstance(msg, bytes):
            m = Za + binascii.b2a_hex(msg).decode()
        else:
            m = Za +binascii.b2a_hex(msg.encode()).decode()
        e = int(sm3.sm3_hash(binascii.a2b_hex(m.encode())), 16)
        x1= self._sGtPk(s,t,pkx,pky)[0]
        if (e+x1)%self.n != r:
            return False
        return True

    def _sGtPk(self,s,t,pkx,pky):
        kt = s
        x1, y1, z1 = 1, 1, 0
        for i in range(32):
            t1 = kt & 0xff
            if t1 != 0:
                x1, y1, z1 = self._point_add(x1, y1, z1, self.kgpre[i][t1 - 1][0], self.kgpre[i][t1 - 1][1], 1)
            kt = kt >> 8
        pkpre = {}
        xp = pkx
        yp = pky
        zp = 1
        xs_p, ys_p, zs_p = self._double_point(xp, yp, zp)
        xn = pkx
        yn = -pky
        zn = 1
        xs_n, ys_n, zs_n = self._double_point(xn, yn, zn)
        pkpre[1] = (xp,yp)
        pkpre[-1] = (xn,yn)
        for i in range(3, 8, 2):
            xp, yp, zp = self._point_add(xp, yp, zp, xs_p, ys_p, zs_p)
            xn, yn, zn = self._point_add(xn, yn, zn, xs_n, ys_n, zs_n)
            pkpre[i] = (self._jacb_to_nor(xp, yp, zp))
            pkpre[-i] = (self._jacb_to_nor(xn, yn, zn))
        k4naf = (self._wnaf(4, t))

        x2, y2, z2 = 1, 1, 0
        for i in k4naf:
            x2, y2, z2 = self._double_point(x2, y2, z2)
            if i !=0:
                x2,y2,z2 = self._point_add(x2,y2,z2,pkpre[i][0],pkpre[i][1],1)

        x,y,z = self._point_add(x1,y1,z1,x2,y2,z2)
        return self._jacb_to_nor(x,y,z)

    def encrypt(self,msg:bytes|str,pk:tuple[int,int],IV:bytes) -> tuple[tuple[int,int],bytes,bytes]:
        if isinstance(msg, str):
            msg = msg.encode()
        k = random.randint(1, self.n - 1)
        C1 = self._kg(k)
        S = self._tP(k,pk[0],pk[1])
        while S==None:
            k = random.randint(1, self.n - 1)
            C1 = self._kg(k)
            S = self._tP(k,pk[0],pk[1])
        key_enc = sm3.KDF_sm3(S[0].to_bytes(32,'big',signed=False)+S[1].to_bytes(32,'big',signed=False),16)
        C2 = sm4_cbc_enc(key_enc,IV,msg)
        C3 = sm3.sm3_hash_bytes(b''.join([S[0].to_bytes(32,'big',signed=False),msg,S[1].to_bytes(32,'big',signed=False)]))
        return C1,C2,C3
        
    def decrypt(self,C:tuple[tuple[int,int],bytes,bytes],IV:bytes) -> bytes:
        C1,C2,C3 = C
        S = self._tP(self.private_key,C1[0],C1[1])
        key_dec = sm3.KDF_sm3(S[0].to_bytes(32,'big',signed=False)+S[1].to_bytes(32,'big',signed=False),16)
        msg = sm4_cbc_dec(key_dec,IV,C2)
        if C3 != sm3.sm3_hash_bytes(b''.join([S[0].to_bytes(32,'big',signed=False),msg,S[1].to_bytes(32,'big',signed=False)])):
            raise Exception("MAC is not correct")
        return msg
        
        


    def _point_add(self, x1, y1, z1, x2, y2, z2):
        if z1 == 0:
            return x2,y2,z2
        if z2 == 0:
            return x1,y1,z1

        t1 = (z1 * z1) % self.p
        l2 = (x2 * t1) % self.p
        t2 = (z1 * t1) % self.p
        t1 = (z2 * z2) % self.p
        l1 = (x1 * t1) % self.p
        l3 = (l1 - l2) % self.p

        l7 = (l1 + l2) % self.p
        l1 = (y2 * t2) % self.p
        t2 = (z2 * t1) % self.p
        l2 = (y1 * t2) % self.p
        l6 = (l2 - l1) % self.p
        t1 = (l6 * l6) % self.p
        t2 = (l3 * l3) % self.p
        xa = (t1 - ((l7 * t2) % self.p)) % self.p
        t1 = (l2 + l1) % self.p
        za = (((z1 * z2) % self.p) * l3) % self.p
        l7 = (((l7 * t2) % self.p) - 2 * xa) % self.p
        t2 = (l3 * t2) % self.p
        ya = ((((l6 * l7) % self.p) - ((
                                                   t1 * t2) % self.p)) * 57896044605178124378210172607010446383125176995962095727210596966644842496000) % self.p  # 提前计算2的逆元
        return xa, ya, za

    def _double_point(self,x,y,z):
        if z == 0:
            return x,y,z

        t1 = (z * z) % self.p
        l1 = (3*(x-t1)*(x+t1)) % self.p  #a%p=-3
        t1 = (y * y) % self.p
        l2 = (4 * x * t1) % self.p
        l3 = (8 * t1 * t1) % self.p
        xd = (((l1 * l1) % self.p) - 2 * l2) % self.p
        yd = (l1 * (l2 - xd) - l3) % self.p
        zd = (2 * y * z) % self.p
        return xd, yd, zd


    def _jacb_to_nor(self,x,y,z):
        if z == 0:
            return None
        z_invs=ModInverse(z,self.p)
        x_nor = (((x*z_invs)%self.p)*z_invs)%self.p
        y_nor = (((((y*z_invs)%self.p)*z_invs)%self.p)*z_invs)%self.p
        return x_nor,y_nor

def exgcd(a, b):
    if b == 0:
        return 1, 0, a
    else:
        x, y, q = exgcd(b, a % b)
        x, y = y, (x - (a // b) * y)
        return x, y, q

# 扩展欧几里得求逆元
def ModInverse(a,p):
    x, y, q = exgcd(a,p)
    if q != 1:
        raise Exception("No solution.")
    else:
        return (x + p) % p #防止负数


