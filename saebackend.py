import sys
from StringIO import StringIO
from datetime import datetime

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.exceptions import ImproperlyConfigured
from filebrowser_safe.storage import StorageMixin
from sae.storage import Connection, Error

from django.core.files.move import file_move_safe
from django.core.files.base import ContentFile

from sae.const import ACCESS_KEY, SECRET_KEY, APP_NAME

STORAGE_BUCKET_NAME = getattr(settings, 'STORAGE_BUCKET_NAME')
STORAGE_ACCOUNT = getattr(settings, 'STORAGE_ACCOUNT', APP_NAME)
STORAGE_ACCESSKEY = getattr(settings, 'STORAGE_ACCESSKEY', ACCESS_KEY)
STORAGE_SECRETKEY = getattr(settings, 'STORAGE_SECRETKEY', SECRET_KEY)
STORAGE_GZIP = getattr(settings, 'STORAGE_GZIP', False)

class Storage(Storage, StorageMixin):
    def __init__(self, bucket_name=STORAGE_BUCKET_NAME,
                 accesskey=STORAGE_ACCESSKEY, secretkey=STORAGE_SECRETKEY,
                 account=STORAGE_ACCOUNT):
        conn = Connection(accesskey, secretkey, account)
        self.bucket = conn.get_bucket(bucket_name)

    def _open(self, name, mode='rb'):
        name = self._normalize_name(name)
        return StorageFile(name, mode, self)

    def _save(self, name, content):
        name = self._normalize_name(name)
        try:
            self.bucket.put_object(name, content)
        except Error, e:
            raise IOError('Storage Error: %s' % e.args)
        return name
        
    def save(self, name, content):
        self._save(name, content)
        
    def delete(self, name):
        name = self._normalize_name(name)
        try:
            self.bucket.delete_object(name)
        except Error, e:
            raise IOError('Storage Error: %s' % e.args)

    def exists(self, name):
        name = self._normalize_name(name)
        try:
            self.bucket.stat_object(name)
        except Error, e:
            if e[0] == 404:
                return False
            raise
        return True
    
    def isfile(self, name):
        return self.exists(name)

    def isdir(self, name):
        if not name:  # Empty name is a directory
            return True
        if self.isfile(name):
            return False
        name = self._normalize_name(name)
        dirlist = self.bucket.list(name)
        for item in dirlist:
            return True
        return False
        
        #name = self._normalize_name(name)
        #try:
        #    attrs = self.bucket.stat_object(name)
        #    if attrs['content_type']!=None:
        #        return False
        #    else: 
        #        return True
        #except Error, e:
        #    raise IOError('Storage Error: %s' % e.args)

    def move(self, old_file_name, new_file_name, allow_overwrite=False):

        if self.exists(new_file_name):
            if allow_overwrite:
                self.delete(new_file_name)
            else:
                raise "The destination file '%s' exists and allow_overwrite is False" % new_file_name

        old_key_name = self._normalize_name(old_file_name)
        new_key_name = self._normalize_name(new_file_name)
        
        if self.isdir(old_key_name):
            #rename the object in the tree
            self.makedirs(new_key_name)
            dirlist = self.bucket.list(old_key_name)
            for item in dirlist:
                old_name = item.name
                len(old_key_name)
                new_name = new_key_name + old_name[len(old_key_name):]
                self.bucket.post_object(item, name=new_name) #cannot update the name, need to change
                
            self.rmtree(old_key_name)
        else:
            #rename the file
            self.bucket.put_object(new_key_name, self.bucket.get_object_contents(old_key_name))
            self.delete(old_file_name)
        
        #k = self.bucket.copy_key(new_key_name, self.bucket.name, old_key_name)

        #if not k:
        #    raise "Couldn't copy '%s' to '%s'" % (old_file_name, new_file_name)

        #self.delete(old_file_name)

    def makedirs(self, name):
        self._save(name + "/.folder", ContentFile(""))
        
    def rmtree(self, name):        
        name = self._normalize_name(name)
        dirlist = self.bucket.list(name)
        for item in dirlist:
            self.delete(item.name)
        #    item.delete()
            
    def listdir(self, path):
        path = self._normalize_name(path)
        directories, files = [], []
        i = len(path)
        try:
            result = self.bucket.list(path=path)
            for obj in result:
                fullname = obj['name']
                name = fullname[i:]
                if obj['content_type']==None:
                    directories.append(name)
                else:
                    files.append(name)            
            return directories, files 
        except Error, e:
            raise IOError('Storage Error: %s' % e.args)
        
    def size(self, name):
        name = self._normalize_name(name)
        try:
            attrs = self.bucket.stat_object(name)
            return attrs.bytes
        except Error, e:
            raise IOError('Storage Error: %s' % e.args)

    def url(self, name):
        name = self._normalize_name(name)
        return self.bucket.generate_url(name)

    def _open_read(self, name):
        name = self._normalize_name(name)
        class _:
            def __init__(self, chunks):
                self.buf = ''
            def read(self, num_bytes=None):
                if num_bytes is None:
                    num_bytes = sys.maxint
                try:
                    while len(self.buf) < num_bytes:
                        self.buf += chunks.next()
                except StopIteration:
                    pass
                except Error, e:
                    raise IOError('Storage Error: %s' % e.args)
                retval = self.buf[:num_bytes]
                self.buf = self.buf[num_bytes:]
                return retval
        chunks = self.bucket.get_object_contents(name, chunk_size=8192)
        return _(chunks)

    def _normalize_name(self, name):
        return name.lstrip('/')
        
    def modified_time(self, name):
        name = self._normalize_name(name)
        try:
            attrs = self.bucket.stat_object(name)
            return datetime.strptime(attrs.last_modified[0:19], "%Y-%m-%dT%H:%M:%S")
        except Error, e:
            raise IOError('Storage Error: %s' % e.args)

class StorageFile(File):
    def __init__(self, name, mode, storage):
        self.name = name
        self.mode = mode
        self.file = StringIO()
        self._storage = storage
        self._is_dirty = False

    @property
    def size(self):
        if hasattr(self, '_size'):
            self._size = self.storage.size()
        return self._size

    def read(self, num_bytes=None):
        if not hasattr(self, '_obj'):
            self._obj = self._storage._open_read(self.name)
        return self._obj.read(num_bytes)

    def write(self, content):
        if 'w' not in self._mode:
            raise AttributeError("File was opened for read-only access.")
        self.file = StringIO(content)
        self._is_dirty = True

    def close(self):
        if self._is_dirty:
            self._storage._save(self.name, self.file.getvalue())
        self.file.close()
