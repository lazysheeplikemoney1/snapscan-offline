package com.snapscan.offline;

import org.json.*;
import java.util.*;

/** Conservative spatial reading rows. Original OCR boxes and correction indices
 * are immutable inputs; rows contain only references to their original indices.
 * Keep constants/formulas synchronized with backend layout.py and H5 textLayout.
 */
public final class TextLayout {
    private TextLayout(){}
    private static final class Box {
        int index;double[][] p;double angle,minU,maxU,minV,maxV,center,height;
    }
    private static final class Row {
        final Box seed;final ArrayList<Box> boxes=new ArrayList<>();
        Row(Box seed){this.seed=seed;boxes.add(seed);}
    }
    public static JSONObject build(JSONArray boxes)throws JSONException {
        JSONArray original=new JSONArray();ArrayList<Box> items=new ArrayList<>();
        if(boxes==null||boxes.length()==0)return result(boxes,original,"original_order");
        for(int i=0;i<boxes.length();i++)original.put(new JSONArray().put(i));
        for(int i=0;i<boxes.length();i++){
            JSONObject item=boxes.optJSONObject(i);Box box=geometry(item,i);
            if(box==null)return result(boxes,original,"original_order");items.add(box);
        }
        double[] angles=new double[items.size()];for(int i=0;i<items.size();i++)angles[i]=items.get(i).angle;
        Arrays.sort(angles);int middle=angles.length/2;
        double angle=angles.length%2==0?(angles[middle-1]+angles[middle])/2:angles[middle];
        double cos=Math.cos(angle),sin=Math.sin(angle);
        for(Box box:items){
            box.minU=Double.POSITIVE_INFINITY;box.maxU=Double.NEGATIVE_INFINITY;box.minV=Double.POSITIVE_INFINITY;box.maxV=Double.NEGATIVE_INFINITY;
            for(double[] point:box.p){double u=point[0]*cos+point[1]*sin,v=-point[0]*sin+point[1]*cos;box.minU=Math.min(box.minU,u);box.maxU=Math.max(box.maxU,u);box.minV=Math.min(box.minV,v);box.maxV=Math.max(box.maxV,v);}
            box.center=(box.minV+box.maxV)/2;box.height=box.maxV-box.minV;
            if(!Double.isFinite(box.height)||box.height<=1e-6)return result(boxes,original,"original_order");
        }
        // Let full-height body text anchor a row before tiny notes/noise. The
        // seed never moves, so this cannot create transitive row-chain drift.
        items.sort(Comparator.comparingDouble((Box box)->-box.height).thenComparingDouble(box->box.center).thenComparingDouble(box->box.minU).thenComparingInt(box->box.index));
        ArrayList<Row> grouped=new ArrayList<>();
        for(Box box:items){
            Row best=null;double bestDistance=Double.POSITIVE_INFINITY;
            for(Row row:grouped){
                double h=Math.min(box.height,row.seed.height),distance=Math.abs(box.center-row.seed.center);
                double overlap=Math.min(box.maxV,row.seed.maxV)-Math.max(box.minV,row.seed.minV);
                if(distance<=.6*h&&overlap>=.3*h){
                    // Curved text lines can have tall, overlapping enclosing
                    // rectangles. They are different rows when their horizontal
                    // spans mostly overlap but their vertical centers differ.
                    // Check every member: a narrow side-note seed must not bridge
                    // two full-width lines. Identical-coordinate duplicates keep
                    // both indices because their center difference is zero.
                    boolean conflict=false;
                    for(Box other:row.boxes){
                        double overlapU=Math.max(0,Math.min(box.maxU,other.maxU)-Math.max(box.minU,other.minU));
                        double widthMin=Math.min(box.maxU-box.minU,other.maxU-other.minU);
                        if(overlapU>.5*widthMin&&Math.abs(box.center-other.center)>.2*Math.min(box.height,other.height)){conflict=true;break;}
                    }
                    if(conflict)continue;
                    double normalized=distance/h;
                    if(normalized<bestDistance){best=row;bestDistance=normalized;}
                }
            }
            if(best==null)grouped.add(new Row(box));else best.boxes.add(box);
        }
        grouped.sort(Comparator.comparingDouble((Row row)->row.seed.center).thenComparingDouble(row->row.seed.minU).thenComparingInt(row->row.seed.index));
        JSONArray rows=new JSONArray();
        for(Row row:grouped){
            row.boxes.sort(Comparator.comparingDouble((Box box)->box.minU).thenComparingInt(box->box.index));
            JSONArray indices=new JSONArray();for(Box box:row.boxes)indices.put(box.index);rows.put(indices);
        }
        return result(boxes,rows,"spatial");
    }
    private static Box geometry(JSONObject item,int index)throws JSONException {
        if(item==null)return null;JSONArray points=item.optJSONArray("box");if(points==null||points.length()!=4)return null;
        Box box=new Box();box.index=index;box.p=new double[4][2];
        for(int i=0;i<4;i++){
            JSONArray point=points.optJSONArray(i);if(point==null||point.length()!=2)return null;
            for(int j=0;j<2;j++){Object value=point.opt(j);if(!(value instanceof Number))return null;double v=((Number)value).doubleValue();if(!Double.isFinite(v))return null;box.p[i][j]=v;}
        }
        for(int i=0;i<4;i++){
            double[] a=box.p[i],b=box.p[(i+1)%4],c=box.p[(i+2)%4];
            double cross=(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);
            if(!Double.isFinite(cross)||cross<=1e-6||Math.hypot(b[0]-a[0],b[1]-a[1])<=1e-6)return null;
        }
        box.angle=Math.atan2((box.p[1][1]-box.p[0][1])+(box.p[2][1]-box.p[3][1]),(box.p[1][0]-box.p[0][0])+(box.p[2][0]-box.p[3][0]));
        return Math.abs(box.angle)>Math.PI/4?null:box;
    }
    private static JSONObject result(JSONArray boxes,JSONArray rows,String mode)throws JSONException {
        StringBuilder text=new StringBuilder();
        for(int r=0;r<rows.length();r++){
            JSONArray indices=rows.getJSONArray(r);StringBuilder line=new StringBuilder();boolean hasPart=false;
            for(int i=0;i<indices.length();i++){
                JSONObject item=boxes.optJSONObject(indices.getInt(i));String part=item==null?"":item.optString("text","");
                if(part.isEmpty())continue;if(hasPart)line.append("  ");line.append(part);hasPart=true;
            }
            if(hasPart){if(text.length()>0)text.append('\n');text.append(line);}
        }
        return new JSONObject().put("rows",rows).put("text",text.toString()).put("mode",mode);
    }
    public static JSONObject apply(JSONObject record)throws JSONException {
        JSONArray boxes=record.optJSONArray("boxes");JSONObject layout=build(boxes);
        if(boxes!=null&&boxes.length()>0)record.put("text",layout.getString("text"));
        return record.put("text_rows",layout.getJSONArray("rows")).put("text_layout",layout.getString("mode"));
    }
    /** Flatten read order without changing the stored boxes or their identity. */
    public static int[] order(JSONObject layout)throws JSONException {
        JSONArray rows=layout.getJSONArray("rows");int count=0;for(int i=0;i<rows.length();i++)count+=rows.getJSONArray(i).length();
        int[] result=new int[count];int next=0;for(int i=0;i<rows.length();i++){JSONArray row=rows.getJSONArray(i);for(int j=0;j<row.length();j++)result[next++]=row.getInt(j);}return result;
    }
}
