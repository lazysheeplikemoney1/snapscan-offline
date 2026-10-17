"""Preflight PDF Unicode round trips with Apache PDFBox 2.0.27 on the host.

Production source is copied into ignored build storage, package names are
swapped to upstream PDFBox, and Android Bitmap is replaced by BufferedImage.
This tests the real PDF/font algorithm but does not replace instrumentation.
No existing source, database, uploads, or locked test image is modified/read.
"""
from pathlib import Path
import json
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]


def main():
    work=ROOT/'build/android-local/export-font-host'
    work.mkdir(parents=True,exist_ok=True)
    source=(ROOT/'mobile/android-local/app/src/main/java/com/snapscan/offline/DocumentExporter.java').read_text(encoding='utf-8')
    source=source.replace('import com.tom_roush.pdfbox.android.PDFBoxResourceLoader;','').replace('PDFBoxResourceLoader.init(context);','')
    source=source.replace('com.tom_roush.pdfbox.','org.apache.pdfbox.')
    source=source.replace('import android.graphics.Bitmap;','').replace('import android.graphics.BitmapFactory;','')
    source=source.replace('Bitmap bitmap=BitmapFactory.decodeFile(image.getAbsolutePath());','java.awt.image.BufferedImage bitmap=javax.imageio.ImageIO.read(image);').replace('bitmap.recycle();','')
    (work/'DocumentExporter.java').write_text(source,encoding='utf-8')
    (work/'Context.java').write_text('package android.content; public class Context { private final android.content.res.AssetManager assets; public Context(java.io.File root){assets=new android.content.res.AssetManager(root);} public android.content.res.AssetManager getAssets(){return assets;} }',encoding='utf-8')
    (work/'AssetManager.java').write_text('package android.content.res; public class AssetManager {private final java.io.File root; public AssetManager(java.io.File root){this.root=root;} public java.io.InputStream open(String name)throws java.io.IOException{return new java.io.FileInputStream(new java.io.File(root,name));}}',encoding='utf-8')
    (work/'PdfCharactersHost.java').write_text(r'''
package com.snapscan.offline;
import java.io.*;import java.nio.file.*;import java.nio.charset.*;import java.util.*;
import org.json.*;import org.apache.pdfbox.pdmodel.*;import org.apache.pdfbox.text.PDFTextStripper;
public class PdfCharactersHost {
 public static void main(String[] args)throws Exception{
  android.content.Context context=new android.content.Context(new File(args[0]));
  JSONArray dictionary=new JSONArray(Files.readString(Paths.get(args[0],"models/characters.json"),StandardCharsets.UTF_8));
  StringBuilder all=new StringBuilder();for(int i=1;i<dictionary.length();i++)all.append(dictionary.getString(i));
  String[] texts={"中文❤❋ɔʌ𣇉"+(char)9+"尾","中文❤❋ɔʌ𣇉"+(char)9+"任意🙂"+new String(Character.toChars(0x10fffd))+"尾",all.toString()};
  JSONArray checks=new JSONArray();
  for(int test=0;test<texts.length;test++)for(String mode:new String[]{"text","searchable"}){
   String text=texts[test];JSONObject record=new JSONObject().put("text",text).put("boxes",new JSONArray().put(new JSONObject().put("text",text).put("box",new JSONArray("[[10,10],[780,10],[780,90],[10,90]]")))).put("image_width",800).put("image_height",300);
   File out=new File(args[2],"unicode-"+test+"-"+mode+".pdf");
   DocumentExporter.pdf(context,record,new File(args[1]),out,mode);
   try(PDDocument pdf=PDDocument.load(out)){
    String extracted=new PDFTextStripper().getText(pdf);
    boolean pass=test==2?extracted.replaceAll("\\s","").contains(text.replaceAll("\\s","")):extracted.contains(text);
    if(!pass)throw new AssertionError("Unicode mismatch test="+test+" mode="+mode+" extracted="+extracted.substring(0,Math.min(200,extracted.length())));
    if(test==1){org.apache.pdfbox.pdmodel.common.filespecification.PDComplexFileSpecification original=(org.apache.pdfbox.pdmodel.common.filespecification.PDComplexFileSpecification)pdf.getDocumentCatalog().getNames().getEmbeddedFiles().getNames().get("SnapScan-original-text.txt");String preserved=new String(original.getEmbeddedFile().toByteArray(),StandardCharsets.UTF_8);if(!preserved.equals(text))throw new AssertionError("Original Unicode attachment mismatch");}
    checks.put(new JSONObject().put("case",test).put("mode",mode).put("pass",true).put("characters",text.codePointCount(0,text.length())).put("pages",pdf.getNumberOfPages()).put("bytes",out.length()));
   }
  }
  Files.writeString(Paths.get(args[2],"checks.json"),checks.toString(2),StandardCharsets.UTF_8);
  System.out.println("PASS "+checks.length()+" host PDF Unicode round trips");
 }
}
''',encoding='utf-8')
    # The host-only dependencies are separate from the official Android runtime.
    deps=ROOT/'build/android-local/export-font-deps'
    logging=next(Path('C:/Users/86197/.gradle/caches/modules-2/files-2.1/commons-logging/commons-logging/1.2').rglob('*.jar'))
    jars=[deps/'pdfbox-2.0.27.jar',deps/'fontbox-2.0.27.jar',logging,ROOT/'build/android-local/host-deps/json-20240303.jar']
    if not all(p.exists() for p in jars):raise FileNotFoundError('Host PDFBox preflight dependencies are absent')
    cp=';'.join(map(str,jars));classes=work/'classes';classes.mkdir(exist_ok=True)
    java=ROOT/'build/android-local/host-jdk/bin'
    subprocess.run([str(java/'javac.exe'),'-encoding','UTF-8','-cp',cp,'-d',str(classes),*map(str,work.glob('*.java'))],check=True)
    image=work/'white.png';Image.new('RGB',(800,300),'white').save(image)
    subprocess.run([str(java/'java.exe'),'-cp',str(classes)+';'+cp,'com.snapscan.offline.PdfCharactersHost',str(ROOT/'mobile/android-local/app/src/main/assets'),str(image),str(work)],check=True)
    report={'android_instrumentation_executed':False,'source_transformed_host_preflight':True,'test_set_read':False,
            'checks':json.loads((work/'checks.json').read_text(encoding='utf-8')),
            'font_manifest':json.loads((ROOT/'mobile/android-local/app/src/main/assets/fonts/font_manifest.json').read_text(encoding='utf-8'))}
    (ROOT/'reports/android_pdf_unicode_host.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
