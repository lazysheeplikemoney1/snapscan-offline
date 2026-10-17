package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Color;
import androidx.test.platform.app.InstrumentationRegistry;
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader;
import com.tom_roush.pdfbox.pdmodel.PDDocument;
import com.tom_roush.pdfbox.pdmodel.font.PDType0Font;
import com.tom_roush.pdfbox.pdmodel.common.filespecification.PDComplexFileSpecification;
import com.tom_roush.pdfbox.text.PDFTextStripper;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import org.junit.Test;

/** Isolated generated pages; no SQLite, history, OCR run or locked test images. */
public class ExportCharactersTest {
    private Context context(){return InstrumentationRegistry.getInstrumentation().getTargetContext();}
    private static byte[] read(InputStream input)throws IOException {
        try(InputStream stream=input;ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] b=new byte[8192];int n;while((n=stream.read(b))!=-1)out.write(b,0,n);return out.toByteArray();
        }
    }
    @Test public void everyModelCharacterHasAnEmbeddedGlyph()throws Exception {
        PDFBoxResourceLoader.init(context());
        JSONArray dictionary=new JSONArray(new String(read(context().getAssets().open("models/characters.json")),StandardCharsets.UTF_8));
        try(PDDocument pdf=new PDDocument();InputStream input=context().getAssets().open("fonts/SnapScanSansSC.ttf")){
            PDType0Font font=PDType0Font.load(pdf,input,true);
            for(int i=1;i<dictionary.length();i++)assertTrue("No glyph for model label index "+i,font.encode(dictionary.getString(i)).length>0);
        }
    }
    @Test public void modelRareCharactersAndTabRoundTripBothPdfModes()throws Exception {
        verify("中文❤❋ɔʌ𣇉\t尾",false);
    }
    @Test public void arbitraryUnicodePreservedByToUnicodeAndOriginalAttachment()throws Exception {
        verify("中文❤❋ɔʌ𣇉\t任意🙂\uDBFF\uDFFD尾",true);
    }
    @Test public void everyModelCharacterRoundTripsBothPdfModes()throws Exception {
        JSONArray dictionary=new JSONArray(new String(read(context().getAssets().open("models/characters.json")),StandardCharsets.UTF_8));
        StringBuilder text=new StringBuilder();for(int i=1;i<dictionary.length();i++)text.append(dictionary.getString(i));
        verify(text.toString(),false);
    }
    private void verify(String text,boolean expectMissing)throws Exception {
        File image=File.createTempFile("unicode-source-",".png",context().getCacheDir());
        Bitmap bitmap=Bitmap.createBitmap(800,300,Bitmap.Config.ARGB_8888);bitmap.eraseColor(Color.WHITE);
        try(FileOutputStream out=new FileOutputStream(image)){assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG,100,out));}
        bitmap.recycle();
        JSONArray points=new JSONArray("[[10,10],[780,10],[780,90],[10,90]]");
        JSONObject record=new JSONObject().put("text",text).put("boxes",new JSONArray().put(new JSONObject().put("text",text).put("box",points)))
            .put("image_width",800).put("image_height",300);
        try {
            for(String mode:new String[]{"text","searchable"}) {
                File output=File.createTempFile("unicode-"+mode+"-",".pdf",context().getCacheDir());
                try {
                    DocumentExporter.pdf(context(),record,image,output,mode);
                    try(PDDocument document=PDDocument.load(output)) {
                        String extracted=new PDFTextStripper().getText(document);
                        if(text.length()>1000)assertTrue(mode+" lost model Unicode",extracted.replaceAll("\\s","").contains(text.replaceAll("\\s","")));
                        else assertTrue(mode+" lost original Unicode: "+extracted,extracted.contains(text));
                        assertTrue("font must actually be embedded",document.getPage(0).getResources().getFontNames().iterator().hasNext());
                        if(expectMissing){
                            assertTrue(document.getDocumentInformation().getCustomMetadataValue("SnapScanUnicodeFallback").contains("preserved"));
                            PDComplexFileSpecification original=(PDComplexFileSpecification)document.getDocumentCatalog().getNames().getEmbeddedFiles().getNames().get("SnapScan-original-text.txt");
                            assertNotNull(original);
                            assertEquals(text,new String(read(original.getEmbeddedFile().createInputStream()),StandardCharsets.UTF_8));
                        }
                    }
                    if(expectMissing&&mode.equals("text"))java.nio.file.Files.copy(output.toPath(),new File(context().getCacheDir(),"unicode-proof-text.pdf").toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                }finally{output.delete();}
            }
        }finally{image.delete();}
    }
}
