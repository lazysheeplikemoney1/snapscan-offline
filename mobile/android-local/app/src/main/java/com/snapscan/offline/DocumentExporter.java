package com.snapscan.offline;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader;
import com.tom_roush.pdfbox.pdmodel.*;
import com.tom_roush.pdfbox.pdmodel.common.PDRectangle;
import com.tom_roush.pdfbox.pdmodel.common.PDStream;
import com.tom_roush.pdfbox.pdmodel.common.filespecification.PDComplexFileSpecification;
import com.tom_roush.pdfbox.pdmodel.common.filespecification.PDEmbeddedFile;
import com.tom_roush.pdfbox.pdmodel.documentinterchange.markedcontent.PDPropertyList;
import com.tom_roush.pdfbox.pdmodel.font.PDType0Font;
import com.tom_roush.pdfbox.cos.*;
import com.tom_roush.pdfbox.pdmodel.graphics.image.LosslessFactory;
import com.tom_roush.pdfbox.pdmodel.graphics.state.RenderingMode;
import com.tom_roush.pdfbox.util.Matrix;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.*;

/** Image-backed searchable PDF and literal-text XLSX, entirely on the device. */
public final class DocumentExporter {
    private static final String MISSING_NOTE="部分字符无内置字形，以方框显示；原文可复制、搜索，并附原始文本。";
    public static void pdf(Context context,JSONObject record,File image,File output,String mode)throws Exception {
        PDFBoxResourceLoader.init(context);
        try(PDDocument pdf=new PDDocument()){
            JSONArray originalBoxes=record.optJSONArray("boxes");JSONObject layout=TextLayout.build(originalBoxes);
            String sourceText=originalBoxes!=null&&originalBoxes.length()>0?layout.getString("text"):record.optString("text","");
            FontPlan plan=new FontPlan(context,pdf,sourceText);
            PDType0Font font=plan.primary;pdf.getDocumentInformation().setTitle("SnapScan");
            if(plan.recoveryUsed)attachOriginal(pdf,sourceText,plan.missingGlyphs);
            if(mode.equals("searchable")){
                Bitmap bitmap=BitmapFactory.decodeFile(image.getAbsolutePath());if(bitmap==null)throw new IOException("扫描图无法读取");
                try{
                    int width=bitmap.getWidth(),height=bitmap.getHeight();if(width!=record.getInt("image_width")||height!=record.getInt("image_height"))throw new IOException("扫描图尺寸与识别坐标不一致");
                    float scale=842f/Math.max(width,height),pageW=width*scale,pageH=height*scale;
                    PDPage page=new PDPage(new PDRectangle(pageW,pageH));pdf.addPage(page);
                    try(PDPageContentStream content=new PDPageContentStream(pdf,page)){
                        content.drawImage(LosslessFactory.createFromImage(pdf,bitmap),0,0,pageW,pageH);
                        JSONArray boxes=record.getJSONArray("boxes");
                        for(int i:TextLayout.order(layout)){
                            JSONObject item=boxes.getJSONObject(i);String text=item.optString("text","");if(text.isEmpty())continue;
                            JSONArray box=item.getJSONArray("box");if(box.length()!=4)throw new IOException("文字坐标无效");
                            float[][] p=new float[4][2];for(int j=0;j<4;j++)for(int k=0;k<2;k++){double v=box.getJSONArray(j).getDouble(k);if(!Double.isFinite(v)||v<0||v>(k==0?width:height))throw new IOException("文字坐标超出扫描图");p[j][k]=(float)v;}
                            String[] parts=text.replace("\r\n","\n").replace('\r','\n').split("\n",-1);
                            for(int j=0;j<parts.length;j++)if(!parts[j].isEmpty()){
                                float top=(float)j/parts.length,bottom=(float)(j+1)/parts.length;
                                float[] tl=lerp(p[0],p[3],top),tr=lerp(p[1],p[2],top),bl=lerp(p[0],p[3],bottom);
                                float dx=(tr[0]-tl[0])*scale,dy=-(tr[1]-tl[1])*scale,ux=(tl[0]-bl[0])*scale,uy=-(tl[1]-bl[1])*scale;
                                float lineW=(float)Math.hypot(dx,dy),lineH=(float)Math.hypot(ux,uy);if(lineW<.01||lineH<.01||dx*uy-dy*ux<=0)throw new IOException("文字坐标无效");
                                float ascent=font.getFontDescriptor().getAscent(),descent=font.getFontDescriptor().getDescent();float size=lineH*1000/(ascent-descent),offset=-descent*size/1000;
                                float natural=plan.width(parts[j],size);if(natural<=0)continue;
                                content.beginText();content.setRenderingMode(RenderingMode.NEITHER);content.setHorizontalScaling(100*lineW/natural);
                                content.setTextMatrix(new Matrix(dx/lineW,dy/lineW,ux/lineH,uy/lineH,bl[0]*scale+ux/lineH*offset,pageH-bl[1]*scale+uy/lineH*offset));plan.show(content,parts[j],size);content.endText();
                            }
                        }
                    }
                }finally{bitmap.recycle();}
            }else{
                String visibleText=sourceText+(plan.missingGlyphs>0?"\n\n"+MISSING_NOTE:"");
                List<String> lines=wrap(visibleText,plan,11,515);int offset=0;
                do{
                    PDPage page=new PDPage(PDRectangle.A4);pdf.addPage(page);
                    try(PDPageContentStream content=new PDPageContentStream(pdf,page)){
                        content.beginText();content.setFont(font,11);content.setLeading(16);content.newLineAtOffset(40,802);
                        for(int row=0;row<47&&offset<lines.size();row++,offset++){plan.show(content,lines.get(offset),11);content.newLine();}content.endText();
                    }
                }while(offset<lines.size());
            }
            pdf.save(output);
        }
    }
    private static float[] lerp(float[] a,float[] b,float t){return new float[]{a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t};}
    private static List<String> wrap(String text,FontPlan plan,float size,float width)throws IOException {
        List<String> out=new ArrayList<>();for(String line:text.replace("\r\n","\n").replace('\r','\n').split("\n",-1)){
            StringBuilder current=new StringBuilder();float used=0;
            for(int i=0;i<line.length();){int cp=line.codePointAt(i);i+=Character.charCount(cp);String ch=new String(Character.toChars(cp));float advance=plan.width(ch,size);
                if(used+advance>width&&current.length()>0){out.add(current.toString());current.setLength(0);used=0;}current.append(ch);used+=advance;
            }out.add(current.toString());
        }return out;
    }

    /** Retain missing Unicode in standard ToUnicode as well as ActualText.
     * PDFBox 2.0.x ignores ActualText while extracting, so ActualText alone is
     * insufficient. The small fully-embedded seed renders an honest square;
     * arbitrary original codepoints never pass through an unsupported cmap.
     */
    private static final class FontPlan {
        final PDType0Font primary;
        final Map<Integer,Glyph> glyphs=new HashMap<>();
        final boolean recoveryUsed;
        final int missingGlyphs;
        FontPlan(Context context,PDDocument pdf,String text)throws Exception {
            try(InputStream input=context.getAssets().open("fonts/SnapScanSansSC.ttf")){primary=PDType0Font.load(pdf,input,true);}
            List<Integer> missing=new ArrayList<>();int missingCount=0;
            String all=text+MISSING_NOTE;
            for(int i=0;i<all.length();){
                int cp=all.codePointAt(i);i+=Character.charCount(cp);
                if(cp=='\r'||cp=='\n'||glyphs.containsKey(cp))continue;
                String symbol=new String(Character.toChars(cp));
                try{
                    if(cp=='\t'||cp<32)throw new IllegalArgumentException("layout/control character");
                    byte[] encoded=primary.encode(symbol);float width=primary.getStringWidth(symbol);primary.addToSubset(cp);
                    glyphs.put(cp,new Glyph(primary,encoded,width));
                }catch(IllegalArgumentException noGlyph){
                    glyphs.put(cp,null);missing.add(cp);if(cp!='\t')missingCount++;
                }
            }
            recoveryUsed=!missing.isEmpty();missingGlyphs=missingCount;
            // A Type0 font has 16-bit character codes. Split only if a document
            // genuinely contains more distinct missing symbols than that space.
            for(int start=0;start<missing.size();start+=65000)
                createRecovery(context,pdf,missing.subList(start,Math.min(start+65000,missing.size())));
        }
        private void createRecovery(Context context,PDDocument pdf,List<Integer> codepoints)throws Exception {
            PDType0Font font;
            try(InputStream input=context.getAssets().open("fonts/SnapScanUnicodeSeed.ttf")){font=PDType0Font.load(pdf,input,false);}
            byte[] square=font.encode("□"),space=font.encode(" ");
            float squareWidth=font.getStringWidth("□"),tabWidth=4*font.getStringWidth(" ");
            byte[] cidToGid=new byte[(codepoints.size()+1)*2];COSArray widths=new COSArray();
            StringBuilder mappings=new StringBuilder("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /SnapScanUnicode def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n");
            for(int index=0;index<codepoints.size();index++){
                if(index%100==0)mappings.append(Math.min(100,codepoints.size()-index)).append(" beginbfchar\n");
                int cp=codepoints.get(index),cid=index+1;byte[] gid=cp=='\t'?space:square;
                cidToGid[cid*2]=gid[0];cidToGid[cid*2+1]=gid[1];
                float width=cp=='\t'?tabWidth:squareWidth;widths.add(new COSFloat(width));
                byte[] encoded=new byte[]{(byte)(cid>>>8),(byte)cid};
                glyphs.put(cp,new Glyph(font,encoded,width));
                mappings.append('<').append(hex(encoded)).append("> <").append(hex(new String(Character.toChars(cp)).getBytes(StandardCharsets.UTF_16BE))).append(">\n");
                if(index%100==99||index==codepoints.size()-1)mappings.append("endbfchar\n");
            }
            mappings.append("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n");
            COSDictionary descendant=(COSDictionary)font.getCOSObject().getCOSArray(COSName.DESCENDANT_FONTS).getObject(0);
            COSArray w=new COSArray();w.add(COSInteger.ONE);w.add(widths);descendant.setItem(COSName.W,w);
            descendant.setItem(COSName.CID_TO_GID_MAP,new PDStream(pdf,new ByteArrayInputStream(cidToGid),COSName.FLATE_DECODE));
            font.getCOSObject().setItem(COSName.TO_UNICODE,new PDStream(pdf,new ByteArrayInputStream(mappings.toString().getBytes(StandardCharsets.US_ASCII)),COSName.FLATE_DECODE));
        }
        float width(String text,float size){float result=0;for(int i=0;i<text.length();){int cp=text.codePointAt(i);i+=Character.charCount(cp);result+=glyphs.get(cp).width*size/1000;}return result;}
        void show(PDPageContentStream content,String text,float size)throws IOException {
            COSDictionary properties=new COSDictionary();byte[] raw=text.getBytes(StandardCharsets.UTF_16BE),bom=new byte[raw.length+2];bom[0]=(byte)0xfe;bom[1]=(byte)0xff;System.arraycopy(raw,0,bom,2,raw.length);
            properties.setItem(COSName.ACTUAL_TEXT,new COSString(bom));content.beginMarkedContent(COSName.getPDFName("Span"),PDPropertyList.create(properties));
            PDType0Font active=null;ByteArrayOutputStream bytes=new ByteArrayOutputStream();
            for(int i=0;i<text.length();){int cp=text.codePointAt(i);i+=Character.charCount(cp);Glyph glyph=glyphs.get(cp);
                if(active!=glyph.font&&active!=null){emit(content,active,bytes.toByteArray(),size);bytes.reset();}
                active=glyph.font;bytes.write(glyph.encoded,0,glyph.encoded.length);
            }
            if(active!=null)emit(content,active,bytes.toByteArray(),size);content.endMarkedContent();
        }
        @SuppressWarnings("deprecation") private static void emit(PDPageContentStream content,PDType0Font font,byte[] bytes,float size)throws IOException {
            content.setFont(font,size);content.appendRawCommands("<"+hex(bytes)+"> Tj\n");
        }
    }
    private static final class Glyph {
        final PDType0Font font;final byte[] encoded;final float width;
        Glyph(PDType0Font font,byte[] encoded,float width){this.font=font;this.encoded=encoded;this.width=width;}
    }
    private static String hex(byte[] bytes){char[] digits="0123456789ABCDEF".toCharArray(),out=new char[bytes.length*2];for(int i=0;i<bytes.length;i++){out[i*2]=digits[(bytes[i]&255)>>>4];out[i*2+1]=digits[bytes[i]&15];}return new String(out);}
    private static void attachOriginal(PDDocument pdf,String text,int missing)throws IOException {
        PDEmbeddedFile embedded=new PDEmbeddedFile(pdf,new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8)));embedded.setSubtype("text/plain");
        PDComplexFileSpecification spec=new PDComplexFileSpecification();spec.setFile("SnapScan-original-text.txt");spec.setFileUnicode("SnapScan-original-text.txt");spec.setEmbeddedFile(embedded);spec.setEmbeddedFileUnicode(embedded);
        PDEmbeddedFilesNameTreeNode tree=new PDEmbeddedFilesNameTreeNode();tree.setNames(Collections.singletonMap("SnapScan-original-text.txt",spec));
        PDDocumentNameDictionary names=new PDDocumentNameDictionary(pdf.getDocumentCatalog());names.setEmbeddedFiles(tree);pdf.getDocumentCatalog().setNames(names);
        pdf.getDocumentInformation().setCustomMetadataValue("SnapScanUnicodeFallback","Original Unicode preserved in ToUnicode, ActualText and UTF-8 attachment; unsupported visible glyphs="+missing);
    }
    public static void excel(JSONArray boxes,File output)throws Exception {
        try(ZipOutputStream zip=new ZipOutputStream(new FileOutputStream(output))){
            entry(zip,"[Content_Types].xml","<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/><Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/></Types>");
            entry(zip,"_rels/.rels","<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>");
            entry(zip,"xl/workbook.xml","<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"OCR结果\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>");
            entry(zip,"xl/_rels/workbook.xml.rels","<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/></Relationships>");
            StringBuilder sheet=new StringBuilder("<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews><cols><col min=\"2\" max=\"2\" width=\"60\" customWidth=\"1\"/></cols><sheetData>");
            row(sheet,1,new String[]{"序号","文本","置信度","坐标"});
            for(int i=0;i<boxes.length();i++){JSONObject b=boxes.getJSONObject(i);row(sheet,i+2,new String[]{String.valueOf(i+1),b.optString("text",""),b.optString("score",""),b.optJSONArray("box")==null?"":b.getJSONArray("box").toString()});}
            sheet.append("</sheetData><autoFilter ref=\"A1:D").append(boxes.length()+1).append("\"/></worksheet>");entry(zip,"xl/worksheets/sheet1.xml",sheet.toString());
        }
    }
    private static void row(StringBuilder out,int number,String[] values){out.append("<row r=\"").append(number).append("\">");for(int i=0;i<values.length;i++)out.append("<c r=\"").append((char)('A'+i)).append(number).append("\" t=\"inlineStr\"><is><t xml:space=\"preserve\">").append(xml(values[i])).append("</t></is></c>");out.append("</row>");}
    private static String xml(String value){return value.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\"","&quot;").replace("'","&apos;").replaceAll("[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F]","");}
    private static void entry(ZipOutputStream zip,String name,String contents)throws IOException {zip.putNextEntry(new ZipEntry(name));zip.write(contents.getBytes(StandardCharsets.UTF_8));zip.closeEntry();}
}
