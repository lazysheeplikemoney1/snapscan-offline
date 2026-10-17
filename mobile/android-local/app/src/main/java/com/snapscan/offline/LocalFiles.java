package com.snapscan.offline;

import android.content.Context;
import java.io.*;
import java.net.URI;
import java.util.UUID;

/** The only file namespace visible to the bundled UI. */
public final class LocalFiles {
    public static final String ORIGIN="https://appassets.androidplatform.net";
    public final File root;
    public LocalFiles(Context context) throws IOException {this(new File(context.getFilesDir(),"local-scans"));}
    public LocalFiles(File root) throws IOException {
        this.root=root.getCanonicalFile();
        if(!this.root.isDirectory()&&!this.root.mkdirs())throw new IOException("无法创建本地文档目录");
    }
    public File getRoot(){return root;}
    public File allocate(String prefix,String extension)throws IOException {
        if(!prefix.matches("[a-z]+")||!extension.matches("jpg|png|pdf|xlsx"))throw new IOException("文件类型无效");
        File target=new File(root,prefix+"_"+UUID.randomUUID().toString().replace("-","")+"."+extension);
        if(!target.createNewFile())throw new IOException("无法创建本地文件");return target;
    }
    public File importImage(InputStream input,String filename)throws IOException {
        File target=allocate("source","jpg"); boolean complete=false;
        try(OutputStream out=new FileOutputStream(target)) {
            byte[] buffer=new byte[65536]; int n; long total=0;
            while((n=input.read(buffer))!=-1){total+=n;if(total>40L*1024*1024)throw new IOException("图片超过40MB，请选择较小图片");out.write(buffer,0,n);}
            if(total==0)throw new IOException("选择的图片为空"); complete=true;
        }finally{if(!complete)target.delete();}
        return target;
    }
    public File resolve(String reference)throws IOException {
        try {
            URI uri=new URI(reference); String path;
            if(uri.isAbsolute()) {
                if(!"https".equals(uri.getScheme())||!"appassets.androidplatform.net".equals(uri.getRawAuthority()))throw new IOException("仅允许本机文件");
                path=uri.getPath();
            }else {if(uri.getRawAuthority()!=null)throw new IOException("无效文件地址");path=uri.getPath();}
            if(uri.getRawQuery()!=null||uri.getRawFragment()!=null||path==null||!path.matches("/uploads/[a-z]+_[0-9a-f]{32}\\.(png|jpg|pdf|xlsx)"))throw new IOException("无效的本机文件地址");
            File target=new File(root,path.substring("/uploads/".length())).getCanonicalFile();
            if(!root.equals(target.getParentFile())||!target.isFile())throw new IOException("本机文件不存在");return target;
        }catch(java.net.URISyntaxException|IllegalArgumentException error){throw new IOException("文件地址无效",error);}
    }
    public String path(File file)throws IOException {if(!file.getCanonicalFile().getParentFile().equals(root))throw new IOException("文件不属于本机文档目录");return "/uploads/"+file.getName();}
    public String url(File file)throws IOException {return ORIGIN+path(file);}
}
